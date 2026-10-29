"""CareCanopy v2: durable, synthetic-only local human-review workflow.

This is a backend proof-of-concept. It is not clinical software, and does not
send any external notification, grant clinical authority, or replace triage.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from datetime import datetime, timezone
import json
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Any


class ReviewError(ValueError):
    """Invalid workflow request or transition."""


class ReviewConflict(ReviewError):
    """A conflicting request was already applied to this case."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{field} must be a nonempty string")
    if len(value) > 250:
        raise ReviewError(f"{field} is too long")
    return value.strip()


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    if "review_packet_json" in item:
        item["review_packet"] = json.loads(item.pop("review_packet_json"))
    return item


class ReviewWorkflow:
    """Transactional local SQLite prototype with restart persistence.

    Supports one review per case ID, deterministic state transitions,
    command idempotency, separate audit events, and an internal work queue.
    The clinician identity is a demo label, NOT an authentication check.
    """

    VALID_ESCALATIONS = {"URGENT_MEDICAL", "NON_URGENT_PROFESSIONAL", "SCOPE_BOUNDARY"}
    VALID_DECISIONS = {"APPROVE_FOLLOWUP", "REQUEST_MORE_INFO", "REFER_URGENT"}

    def __init__(self, db_path: str | Path, *, profile_db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path)
        # Explicitly configured for the CareCanopy app; standalone legacy
        # fixture tests can run without a synthetic patient database.
        if profile_db_path is None:
            neighbor = Path(self.db_path).parent / "synthetic_patients.sqlite3"
            profile_db_path = neighbor if neighbor.is_file() else None
        self.profile_db_path = str(profile_db_path) if profile_db_path is not None else None
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=20, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout = 20000")
        c.execute("PRAGMA foreign_keys = ON")
        if self.profile_db_path is not None:
            if not Path(self.profile_db_path).is_file():
                c.close()
                raise ReviewError("Synthetic patient store is missing; cannot verify review context")
            c.execute("ATTACH DATABASE ? AS patient_context", (self.profile_db_path,))
        return c

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except Exception:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    def _init_schema(self):
        with closing(self._connect()) as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS reviews (
                    case_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    escalation_type TEXT NOT NULL,
                    review_packet_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT,
                    reviewer_label TEXT,
                    review_note TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    profile_version INTEGER,
                    profile_digest TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    command_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES reviews(case_id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_case ON events(case_id,event_id);
                CREATE TABLE IF NOT EXISTS internal_handoffs (
                    case_id TEXT PRIMARY KEY,
                    handoff_kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    delivery_scope TEXT NOT NULL DEFAULT 'IN_APP_ONLY',
                    external_delivery_status TEXT NOT NULL DEFAULT 'NOT_CONFIGURED',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES reviews(case_id)
                );
            """)
            # Existing reviews remain immutable. Unbound legacy reviews are
            # read-only once a synthetic patient store is configured.
            columns = {row[1] for row in c.execute("PRAGMA table_info(reviews)")}
            for column, kind in (("profile_version", "INTEGER"), ("profile_digest", "TEXT")):
                if column not in columns:
                    c.execute(f"ALTER TABLE reviews ADD COLUMN {column} {kind}")

    def _row(self, c: sqlite3.Connection, case_id: str):
        return c.execute("SELECT * FROM reviews WHERE case_id=?", (case_id,)).fetchone()

    @staticmethod
    def _fingerprint(*parts) -> str:
        return sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _already_applied(self, c: sqlite3.Connection, command_id: str, case_id: str,
                         event_type: str, fingerprint: str) -> bool:
        found = c.execute("SELECT case_id,event_type,fingerprint FROM events WHERE command_id=?", (command_id,)).fetchone()
        if not found:
            return False
        if (found["case_id"] != case_id or found["event_type"] != event_type or
            found["fingerprint"] != fingerprint):
            raise ReviewConflict("command_id was reused with different operation or payload")
        return True

    def _event(self, c, case_id, command_id, event_type, detail, fingerprint):
        c.execute("INSERT INTO events (case_id,command_id,event_type,detail,fingerprint,created_at) VALUES (?,?,?,?,?,?)",
                  (case_id, command_id, event_type, detail, fingerprint, _now()))

    @staticmethod
    def _context_digest(profile: sqlite3.Row) -> str:
        # A reversible edit must still invalidate old authorizations: version
        # participates in the digest even if all fields return to old values.
        fields = {k: profile[k] for k in
                  ("patient_id", "scenario_id", "age", "weeks_post_stroke",
                   "baseline", "approved_plan", "context_version")}
        return sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _profile(self, c: sqlite3.Connection, patient_id: str):
        if not self.profile_db_path:
            return None
        try:
            return c.execute("SELECT * FROM patient_context.synthetic_profiles WHERE patient_id=?",
                             (patient_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            raise ReviewConflict("Synthetic profile schema unavailable; review blocked") from exc

    def _require_current_context(self, c: sqlite3.Connection, review: sqlite3.Row) -> None:
        if not self.profile_db_path:
            return  # Standalone legacy workflow with no configured patient store.
        profile = self._profile(c, review["patient_id"])
        if profile is None:
            raise ReviewConflict("Synthetic patient profile missing; review blocked")
        if review["profile_version"] is None or not review["profile_digest"]:
            raise ReviewConflict("Historical review lacks verified context binding; run a new follow-up")
        if (profile["context_version"] != review["profile_version"] or
            self._context_digest(profile) != review["profile_digest"]):
            raise ReviewConflict("Patient context changed; historical review cannot be reused")

    def create_review(self, case_id: str, patient_id: str, review_packet: dict,
                      *, expected_context_version: int | None = None,
                      expected_patient_context: dict | None = None) -> dict:
        case_id = _required(case_id, "case_id")
        patient_id = _required(patient_id, "patient_id")
        if not isinstance(review_packet, dict):
            raise ReviewError("review_packet must be a dictionary")
        escalation_type = review_packet.get("escalation_type")
        if escalation_type not in self.VALID_ESCALATIONS:
            raise ReviewError("A valid escalation_type is required")
        if review_packet.get("case_id") not in (None, case_id):
            raise ReviewError("review_packet case_id mismatch")
        packet_json = json.dumps(review_packet, ensure_ascii=False, sort_keys=True)
        command_id = f"review:create:{case_id}"
        with self._transaction() as c:
            profile_version = profile_digest = None
            if self.profile_db_path:
                profile = self._profile(c, patient_id)
                if profile is None:
                    raise ReviewConflict("No matching synthetic patient profile; review blocked")
                if not case_id.startswith(profile["scenario_id"] + "_UI_"):
                    raise ReviewConflict("Review case does not belong to the synthetic patient")
                if (type(expected_context_version) is not int or
                    profile["context_version"] != expected_context_version):
                    raise ReviewConflict("Patient context changed during assessment; rerun follow-up")
                fields = ("age", "weeks_post_stroke", "baseline", "approved_plan")
                if (not isinstance(expected_patient_context, dict) or
                    any(expected_patient_context.get(k) != profile[k] for k in fields)):
                    raise ReviewConflict("Assessment patient snapshot does not match saved context")
                profile_version = profile["context_version"]
                profile_digest = self._context_digest(profile)
            existing = self._row(c, case_id)
            if existing:
                if (existing["patient_id"] != patient_id or
                    existing["escalation_type"] != escalation_type or
                    existing["review_packet_json"] != packet_json):
                    raise ReviewConflict("Case ID already used with different input")
                self._require_current_context(c, existing)
                return _decode(existing)
            current = _now()
            c.execute("""INSERT INTO reviews
                      (case_id,patient_id,escalation_type,review_packet_json,status,created_at,updated_at,profile_version,profile_digest)
                      VALUES (?,?,?,?,?,?,?,?,?)""",
                      (case_id, patient_id, escalation_type, packet_json, "REVIEW_PENDING",
                       current, current, profile_version, profile_digest))
            self._event(c, case_id, command_id, "REVIEW_CREATED", "Awaiting simulated clinician review; no external message sent", self._fingerprint(case_id, patient_id, packet_json))
            return _decode(self._row(c, case_id))

    def decide(self, case_id: str, reviewer_label: str, decision: str,
               note: str, command_id: str) -> dict:
        case_id = _required(case_id, "case_id")
        reviewer_label = _required(reviewer_label, "reviewer_label")
        command_id = _required(command_id, "command_id")
        note = _required(note, "note")
        if decision not in self.VALID_DECISIONS:
            raise ReviewError("Unsupported reviewer decision")
        with self._transaction() as c:
            row = self._row(c, case_id)
            if row is None:
                raise ReviewError("No review task for case")
            # Context verification must be inside the SQLite write transaction,
            # before idempotent replies or any state/event mutation.
            self._require_current_context(c, row)
            fingerprint = self._fingerprint(case_id, reviewer_label, decision, note)
            if self._already_applied(c, command_id, case_id, "REVIEW_DECIDED", fingerprint):
                return _decode(row)
            if row["status"] != "REVIEW_PENDING":
                raise ReviewConflict(f"Case is {row['status']}; cannot decide again")
            if row["escalation_type"] == "URGENT_MEDICAL" and decision != "REFER_URGENT":
                raise ReviewError("Urgent medical escalation cannot be cleared or delayed by review")
            status = "AWAITING_INFORMATION" if decision == "REQUEST_MORE_INFO" else "ACTION_PENDING"
            c.execute("""UPDATE reviews SET status=?,decision=?,reviewer_label=?,review_note=?,
                       updated_at=?,version=version+1 WHERE case_id=?""",
                      (status, decision, reviewer_label, note, _now(), case_id))
            self._event(c, case_id, command_id, "REVIEW_DECIDED", f"{decision}; demo reviewer: {reviewer_label}", fingerprint)
            return _decode(self._row(c, case_id))

    def publish_internal_handoff(self, case_id: str, command_id: str) -> dict:
        """Persist an internal work item, NOT a sent email/SMS/clinical alert.

        Atomic publication and completion make retries safe across restarts.
        """
        case_id = _required(case_id, "case_id")
        command_id = _required(command_id, "command_id")
        with self._transaction() as c:
            row = self._row(c, case_id)
            if row is None:
                raise ReviewError("No review task for case")
            self._require_current_context(c, row)
            fingerprint = self._fingerprint(case_id, "PUBLISH_INTERNAL_HANDOFF")
            if self._already_applied(c, command_id, case_id, "INTERNAL_HANDOFF_PUBLISHED", fingerprint):
                return _decode(row)
            if row["status"] != "ACTION_PENDING":
                raise ReviewConflict("There is no pending approved action")
            decision = row["decision"]
            if decision == "APPROVE_FOLLOWUP":
                kind = "NONCLINICAL_FOLLOWUP"
                message = "A simulated reviewer approved an existing-plan follow-up. Frontline staff must verify and act within authorised scope."
            elif decision == "REFER_URGENT":
                kind = "URGENT_HANDOFF_RECORD"
                message = "Urgent escalation remains active. Immediate emergency guidance is separate; this creates an INTERNAL handoff record, not a call or dispatch."
            else:
                raise ReviewError("This decision cannot publish an action")
            c.execute("""INSERT OR IGNORE INTO internal_handoffs
                       (case_id,handoff_kind,message,created_at) VALUES (?,?,?,?)""",
                      (case_id, kind, message, _now()))
            c.execute("UPDATE reviews SET status='COMPLETED_INTERNAL',updated_at=?,version=version+1 WHERE case_id=?",
                      (_now(), case_id))
            self._event(c, case_id, command_id, "INTERNAL_HANDOFF_PUBLISHED", "Saved to in-app queue; external delivery not configured", fingerprint)
            return _decode(self._row(c, case_id))

    def review_context_state(self, case_id: str) -> str:
        """Read-only UI hint using the backend's own context validation.

        Returns current, unverified, changed or unavailable. This is NOT an
        authorization decision: decide() and publish_internal_handoff()
        separately recheck the binding inside their write transactions.
        """
        with closing(self._connect()) as c:
            row = self._row(c, case_id)
            if row is None or not self.profile_db_path:
                return "unavailable"
            if row["profile_version"] is None or not row["profile_digest"]:
                return "unverified"
            try:
                self._require_current_context(c, row)
            except ReviewConflict:
                return "changed"
            return "current"

    def get(self, case_id: str) -> dict | None:
        with closing(self._connect()) as c:
            row = self._row(c, case_id)
            return _decode(row) if row else None

    def list_reviews(self, limit: int = 100) -> list[dict]:
        with closing(self._connect()) as c:
            return [_decode(r) for r in c.execute("SELECT * FROM reviews ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 1000),))]

    def audit_events(self, case_id: str) -> list[dict]:
        with closing(self._connect()) as c:
            return [dict(r) for r in c.execute("SELECT event_id,event_type,detail,created_at FROM events WHERE case_id=? ORDER BY event_id", (case_id,))]

    def internal_handoff(self, case_id: str) -> dict | None:
        with closing(self._connect()) as c:
            row = c.execute("SELECT * FROM internal_handoffs WHERE case_id=?", (case_id,)).fetchone()
            return dict(row) if row else None
