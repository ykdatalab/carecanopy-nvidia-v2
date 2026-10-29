#!/usr/bin/env python3
"""Offline, network-blocked dry run of the CareCanopy Nebius request builder.

Calls the app's own ``route_case_nebius`` with a capturing transport, so the
payload shape is exactly what the app would send, then stops before sending.
Network access (urllib and sockets) is blocked for the whole run, even when
real Nebius environment variables are present. Nothing is read from or written
to SQLite.

Prints shape information only: never API keys, model ID strings, prompt or
message contents, patient observations, or provider responses.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import sys
from unittest import mock
import urllib.request

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import nebius_agent  # noqa: E402
from app.nebius_agent import NebiusRouteError, route_case_nebius  # noqa: E402
from app.synthetic_profiles import case_with_demo_profile  # noqa: E402

PLAYGROUND_MODEL = "nvidia/Nemotron-3_5-Lightning"
# Placeholders only shape the payload; they are never sent anywhere.
PLACEHOLDER_KEY = "dry-run-placeholder"
PLACEHOLDER_MODEL = "nvidia/dry-run-placeholder-nemotron"
SCENARIO_ID = "DEV04"
FLAG_VARS = ("NEBIUS_BASE_URL", "CARECANOPY_NEBIUS_JSON_MODE", "CARECANOPY_NEBIUS_MINIMAL_REQUEST",
             "CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS", "CARECANOPY_NEBIUS_DISABLE_THINKING")


class NetworkBlocked(RuntimeError):
    """Raised if anything tries to reach the network during the dry run."""


class _Captured(Exception):
    def __init__(self, url: str, payload: dict):
        super().__init__("captured")
        self.url = url
        self.payload = payload


def _refuse(*_args, **_kwargs):
    raise NetworkBlocked("network access is blocked in the Nebius dry run")


@contextmanager
def network_blocked():
    with mock.patch.object(urllib.request, "urlopen", _refuse), \
            mock.patch.object(nebius_agent, "urlopen", _refuse), \
            mock.patch.object(socket.socket, "connect", _refuse), \
            mock.patch.object(socket.socket, "connect_ex", _refuse), \
            mock.patch.object(socket, "create_connection", _refuse):
        yield


def _synthetic_case() -> dict:
    """The app's own case shape for DEV04, built in memory from the fixture."""
    cases = json.loads((ROOT / "data" / "dev_cases.json").read_text(encoding="utf-8"))
    scenario = next(c for c in cases if c.get("id") == SCENARIO_ID)
    # Same shape as a freshly seeded fixture profile (context_version starts at 1).
    profile = dict(scenario["patient_context"], patient_id="SYN-" + SCENARIO_ID,
                   scenario_id=SCENARIO_ID, context_version=1)
    case = case_with_demo_profile(scenario, profile)
    case["id"] = SCENARIO_ID + "_UI_DRYRUN"
    case["synthetic_patient_id"] = "SYN-" + SCENARIO_ID
    return case


def _model_status(value: str | None, *, known: bool = True) -> str:
    if not known:
        return "unknown (entered interactively in the launcher; not recoverable)"
    if not value or not value.strip():
        return "not set"
    return "match" if value.strip() == PLAYGROUND_MODEL else "differs from Playground ID"


def describe(env: dict, *, model_known: bool = True) -> dict:
    """Build (never send) the request for ``env`` and summarize its shape."""
    model_status = _model_status(env.get("NEBIUS_MODEL_ID"), known=model_known)
    run_env = dict(env)
    run_env.setdefault("CARECANOPY_ENABLE_NEBIUS", "1")
    if not run_env.get("NEBIUS_API_KEY", "").strip():
        run_env["NEBIUS_API_KEY"] = PLACEHOLDER_KEY
    if not model_known or not run_env.get("NEBIUS_MODEL_ID", "").strip():
        run_env["NEBIUS_MODEL_ID"] = PLACEHOLDER_MODEL

    def capture(_key, url, payload):
        raise _Captured(url, payload)

    if not model_known:
        key_status = "unknown (entered interactively in the launcher; not recoverable)"
    else:
        key_status = "yes" if env.get("NEBIUS_API_KEY", "").strip() else "no"
    summary = {"model_id": model_status, "api_key_present": key_status}
    case = _synthetic_case()
    with network_blocked(), mock.patch.dict(os.environ, run_env, clear=True):
        try:
            route_case_nebius(case, transport=capture)
        except _Captured as got:
            payload = got.payload
            summary.update({
                "endpoint": nebius_agent._endpoint_label(got.url),
                "payload_profile": nebius_agent._request_profile_from_payload(payload),
                "payload_keys": ", ".join(sorted(payload)),
                "max_tokens": payload.get("max_tokens", "absent"),
                "response_format": "present" if "response_format" in payload else "absent",
                "chat_template_kwargs": ("enable_thinking=false"
                                         if payload.get("chat_template_kwargs") == {"enable_thinking": False}
                                         else "absent" if "chat_template_kwargs" not in payload
                                         else "other"),
                "messages": len(payload.get("messages", [])),
            })
        except NebiusRouteError as exc:
            # Fixed, app-controlled text (e.g. "not allowlisted"); no provider data.
            summary["stopped_before_request"] = str(exc)
    return summary


def launcher_env(current: dict) -> dict:
    """Reconstruct what launch_private_nebius.py would add; it is never run."""
    env = {name: current[name] for name in FLAG_VARS if name in current}
    env["CARECANOPY_ENABLE_NEBIUS"] = "1"
    return env


def main() -> int:
    current = dict(os.environ)
    inherited = [name for name in FLAG_VARS if name in current]
    print("CareCanopy Nebius dry run: OFFLINE. Network is blocked; nothing is sent.")
    print("Synthetic case: " + SCENARIO_ID + " (fixture, in memory; no SQLite access)")
    print()
    print("[1] Current terminal environment")
    for name, value in describe(current).items():
        print(f"    {name}: {value}")
    print()
    print("[2] Launcher-effective profile: RECONSTRUCTION, not historical runtime evidence")
    print("    launch_private_nebius.py sets CARECANOPY_ENABLE_NEBIUS=1, NEBIUS_MODEL_ID and")
    print("    NEBIUS_API_KEY (both typed at its prompts). It never sets NEBIUS_BASE_URL,")
    print("    CARECANOPY_NEBIUS_JSON_MODE, CARECANOPY_NEBIUS_MINIMAL_REQUEST,")
    print("    CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS or CARECANOPY_NEBIUS_DISABLE_THINKING.")
    if inherited:
        print("    Inherited from this terminal ONLY if they were also set in the shell that ran")
        print("    the launcher: " + ", ".join(inherited))
    else:
        print("    No JSON-mode, minimal or base-URL flags are set in this terminal.")
    for name, value in describe(launcher_env(current), model_known=False).items():
        print(f"    {name}: {value}")
    print()
    print("No model listing or inference was attempted. Those require separate approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
