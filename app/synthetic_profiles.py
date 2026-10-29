"""Persistent SYNTHETIC patient context and follow-up observations for demos.

This file is deliberately independent of the clinical safety gate, clinician
review state machine and AWS v1. It does NOT verify clinical authorization,
collect real patient identity, or demonstrate clinical validation.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3


class SyntheticProfileError(ValueError):
    """Invalid synthetic-only input or incompatible stored identity."""


class SyntheticProfileConflict(SyntheticProfileError):
    """An existing immutable event ID was reused with different contents."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scenario_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"DEV[0-9]{2}", value):
        raise SyntheticProfileError("Only the built-in DEV## synthetic scenarios are supported")
    return value


def _text(value: str, name: str, max_length: int = 600) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise SyntheticProfileError(f"{name} must be 1–{max_length} characters")
    return value.strip()


def _integer(value: int, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SyntheticProfileError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _as_dict(row):
    return dict(row) if row is not None else None


class SyntheticPatientStore:
    """Local SQLite demo storage; no cross-user authentication or cloud sync."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS synthetic_profiles (
                    patient_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL UNIQUE,
                    age INTEGER NOT NULL,
                    weeks_post_stroke INTEGER NOT NULL,
                    baseline TEXT NOT NULL,
                    approved_plan TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_demo_edit_at TEXT,
                    context_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS synthetic_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id TEXT NOT NULL,
                    case_id TEXT NOT NULL UNIQUE,
                    report TEXT NOT NULL,
                    source_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES synthetic_profiles(patient_id)
                );
                CREATE INDEX IF NOT EXISTS idx_synthetic_obs_patient
                    ON synthetic_observations(patient_id, observation_id);
                CREATE TABLE IF NOT EXISTS synthetic_profile_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES synthetic_profiles(patient_id)
                );
            """)
            # Forward-only migration for existing October 2026 local demo DBs.
            columns = {row[1] for row in con.execute("PRAGMA table_info(synthetic_profiles)")}
            if "context_version" not in columns:
                con.execute("ALTER TABLE synthetic_profiles ADD COLUMN context_version INTEGER NOT NULL DEFAULT 1")

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=20, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=20000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @staticmethod
    def patient_id_for(scenario_id: str) -> str:
        return "SYN-" + _scenario_id(scenario_id)

    def get(self, patient_id: str) -> dict | None:
        with closing(self._connect()) as con:
            return _as_dict(con.execute(
                "SELECT * FROM synthetic_profiles WHERE patient_id=?", (patient_id,)
            ).fetchone())

    def seed_from_fixture(self, scenario: dict) -> dict:
        """Seed one DEV## fixture once; reopening the app never erases demo edits."""
        scenario_id = _scenario_id(scenario.get("id"))
        context = scenario.get("patient_context")
        if not isinstance(context, dict):
            raise SyntheticProfileError("Synthetic fixture lacks patient_context")
        age = _integer(context.get("age"), "age", 18, 120)
        weeks = _integer(context.get("weeks_post_stroke"), "weeks_post_stroke", 0, 520)
        baseline = _text(context.get("baseline"), "baseline")
        approved_plan = _text(context.get("approved_plan"), "approved_plan")
        patient_id = self.patient_id_for(scenario_id)
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT * FROM synthetic_profiles WHERE patient_id=?", (patient_id,)).fetchone()
                if row is None:
                    moment = _now()
                    con.execute("""INSERT INTO synthetic_profiles
                        (patient_id,scenario_id,age,weeks_post_stroke,baseline,approved_plan,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?)""", (patient_id, scenario_id, age, weeks, baseline, approved_plan, moment, moment))
                    con.execute("""INSERT INTO synthetic_profile_audit
                        (patient_id,event_type,detail_json,created_at) VALUES (?,?,?,?)""",
                        (patient_id, "FIXTURE_SEEDED", json.dumps({"scenario_id": scenario_id}), moment))
                elif row["scenario_id"] != scenario_id or row["approved_plan"] != approved_plan:
                    raise SyntheticProfileConflict("Fixture identity or locked approved plan changed")
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return self.get(patient_id)

    def update_demo_context(self, patient_id: str, *, age: int, weeks_post_stroke: int,
                            baseline: str) -> dict:
        """Edit *demo* demographics/baseline, never the locked approved plan."""
        age = _integer(age, "age", 18, 120)
        weeks_post_stroke = _integer(weeks_post_stroke, "weeks_post_stroke", 0, 520)
        baseline = _text(baseline, "baseline")
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT * FROM synthetic_profiles WHERE patient_id=?", (patient_id,)).fetchone()
                if row is None:
                    raise SyntheticProfileError("Unknown synthetic patient")
                updated = {"age": age, "weeks_post_stroke": weeks_post_stroke, "baseline": baseline}
                previous = {name: row[name] for name in updated}
                if updated != previous:
                    moment = _now()
                    con.execute("""UPDATE synthetic_profiles
                        SET age=?,weeks_post_stroke=?,baseline=?,updated_at=?,last_demo_edit_at=?,
                            context_version=context_version+1 WHERE patient_id=?""",
                        (age, weeks_post_stroke, baseline, moment, moment, patient_id))
                    con.execute("""INSERT INTO synthetic_profile_audit
                        (patient_id,event_type,detail_json,created_at) VALUES (?,?,?,?)""",
                        (patient_id, "DEMO_CONTEXT_UPDATED",
                         json.dumps({"before": previous, "after": updated}, ensure_ascii=False), moment))
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return self.get(patient_id)

    def save_observation(self, patient_id: str, *, case_id: str, report: str,
                         source_mode: str) -> dict:
        """One immutable observation per unique routing case; retries are idempotent."""
        case_id = _text(case_id, "case_id", 130)
        report = _text(report, "report", 4000)
        if source_mode not in {"OFFLINE_SCRIPTED", "AWS_BEDROCK_V1", "NEBIUS_NEMOTRON"}:
            raise SyntheticProfileError("Unrecognized execution mode")
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                profile = con.execute("SELECT scenario_id FROM synthetic_profiles WHERE patient_id=?", (patient_id,)).fetchone()
                if profile is None:
                    raise SyntheticProfileError("Unknown synthetic patient")
                if not case_id.startswith(profile["scenario_id"] + "_UI_"):
                    raise SyntheticProfileConflict("Observation case ID does not belong to this synthetic patient")
                existing = con.execute("SELECT * FROM synthetic_observations WHERE case_id=?", (case_id,)).fetchone()
                if existing:
                    if (existing["patient_id"], existing["report"], existing["source_mode"]) != (patient_id, report, source_mode):
                        raise SyntheticProfileConflict("Repeated case ID with different observation or mode")
                else:
                    con.execute("""INSERT INTO synthetic_observations
                        (patient_id,case_id,report,source_mode,created_at) VALUES (?,?,?,?,?)""",
                        (patient_id, case_id, report, source_mode, _now()))
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        with closing(self._connect()) as con:
            return _as_dict(con.execute("SELECT * FROM synthetic_observations WHERE case_id=?", (case_id,)).fetchone())

    def observations(self, patient_id: str, limit: int = 10) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 30:
            raise SyntheticProfileError("History limit must be 1–30")
        with closing(self._connect()) as con:
            return [_as_dict(row) for row in con.execute(
                """SELECT * FROM synthetic_observations WHERE patient_id=?
                   ORDER BY observation_id DESC LIMIT ?""", (patient_id, limit)
            )]

    def audit(self, patient_id: str) -> list[dict]:
        with closing(self._connect()) as con:
            return [_as_dict(row) for row in con.execute(
                "SELECT * FROM synthetic_profile_audit WHERE patient_id=? ORDER BY audit_id", (patient_id,)
            )]


def case_with_demo_profile(scenario: dict, profile: dict, prior_observations: list[dict] | None = None) -> dict:
    """Carry stored context to the next route; original fixture remains untouched."""
    scenario_id = _scenario_id(scenario.get("id"))
    if profile.get("patient_id") != "SYN-" + scenario_id or profile.get("scenario_id") != scenario_id:
        raise SyntheticProfileConflict("Profile and selected case do not match")
    updated = dict(scenario)
    updated["patient_context"] = {
        "age": profile["age"],
        "weeks_post_stroke": profile["weeks_post_stroke"],
        "baseline": profile["baseline"],
        "approved_plan": profile["approved_plan"],
    }
    # Historical reports are unverified observations, not clinical conclusions.
    # They are copied only into development routing context, never into the
    # frozen evaluation fixtures. The current report is recorded separately.
    history = []
    for item in (prior_observations or [])[:5]:
        if item.get("patient_id") != profile["patient_id"]:
            raise SyntheticProfileConflict("Historical observation belongs to another patient")
        history.append({
            "report": _text(item.get("report"), "prior_report", 4000),
            "recorded_at": item.get("created_at", ""),
            "source_mode": item.get("source_mode", "UNVERIFIED"),
        })
    if history:
        updated["patient_context"]["previous_unverified_reports"] = history
    updated["synthetic_patient_id"] = profile["patient_id"]
    # Carry an immutable expected version from assessment start to review creation.
    # The backend rejects a delayed routing result if the profile changed meanwhile.
    updated["synthetic_context_version"] = profile["context_version"]
    return updated


def review_predates_demo_context_edit(profile: dict, review: dict | None) -> bool:
    """A saved review is historical if the synthetic context changed since it was created."""
    if not review or not profile.get("last_demo_edit_at"):
        return False
    from datetime import datetime
    return datetime.fromisoformat(profile["last_demo_edit_at"]) > datetime.fromisoformat(review["created_at"])
