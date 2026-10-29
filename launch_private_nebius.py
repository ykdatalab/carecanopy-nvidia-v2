#!/usr/bin/env python3
"""Launch a local-only CareCanopy Nemotron test without saving the API key.

Requires installed Streamlit (carecanopy conda environment). Does not make an
API request itself. Select live mode and press Analyze to spend credits.

Applies the verified LIVE profile (eu-north1, minimal request, max_tokens 2048,
reasoning off). A conflicting value already set in the shell stops the launch
instead of silently changing the configuration.
"""
from getpass import getpass
import os
from pathlib import Path
import subprocess
import sys

VERIFIED_MODEL = 'nvidia/Nemotron-3_5-Lightning'
# Verified end to end on 2026-10-29 (HTTP 200, validated response, Safety Gate evaluated).
VERIFIED_PROFILE = {
    'NEBIUS_BASE_URL': 'https://api.tokenfactory.eu-north1.nebius.com/v1',
    'CARECANOPY_NEBIUS_JSON_MODE': '0',
    'CARECANOPY_NEBIUS_MINIMAL_REQUEST': '1',
    'CARECANOPY_NEBIUS_MINIMAL_MAX_TOKENS': '2048',
    'CARECANOPY_NEBIUS_DISABLE_THINKING': '1',
}


def profile_conflicts(base_env):
    """Names of inherited variables that differ from the verified profile."""
    return [name for name, verified in VERIFIED_PROFILE.items()
            if base_env.get(name) is not None and base_env[name].strip().rstrip('/') != verified]


def describe_request(env):
    """Offline, network-blocked description of the request the app would build (no key shown)."""
    import diagnose_nebius
    summary = diagnose_nebius.describe(env)
    return {k: summary.get(k, '-') for k in ('endpoint', 'payload_profile', 'max_tokens', 'chat_template_kwargs')}


def main():
    root = Path(__file__).resolve().parent
    print('CareCanopy v2 — synthetic-only local Nebius launcher')
    print('No real patient data. No credits will be spent by this launcher itself.')
    print('Only continue AFTER confirming credit/billing preferences yourself.')
    conflicts = profile_conflicts(os.environ)
    if conflicts:
        print('STOP: this shell sets Nebius values that differ from the verified LIVE profile:')
        for name in conflicts:
            print(f'  {name} (verified value: {VERIFIED_PROFILE[name]})')
        print('Unset them or match the verified values, then run the launcher again. Nothing launched.')
        return 3
    confirmation = input('Type ENABLE to allow optional paid model calls from the app: ').strip()
    if confirmation != 'ENABLE':
        print('Cancelled. No Streamlit process launched.')
        return 2
    model = input(f'Model ID [press Enter for verified {VERIFIED_MODEL}]: ').strip() or VERIFIED_MODEL
    if not model.startswith('nvidia/') or 'nemotron' not in model.lower():
        print('Model ID must be an NVIDIA Nemotron from your account catalog.')
        return 2
    if model != VERIFIED_MODEL:
        print(f'WARNING: model differs from the verified {VERIFIED_MODEL}; LIVE results are unverified.')
    key = getpass('Paste NEBIUS_API_KEY (hidden, not saved to a file): ').strip()
    if not key:
        print('No API key provided; cancelled.')
        return 2
    env = os.environ.copy()
    env.update(VERIFIED_PROFILE)
    env.update({'CARECANOPY_ENABLE_NEBIUS': '1', 'NEBIUS_MODEL_ID': model,
                'NEBIUS_API_KEY': key})
    # Local Streamlit defaults to localhost; do not port-forward this proof mode.
    env['STREAMLIT_SERVER_ADDRESS'] = '127.0.0.1'
    request = describe_request(env)
    print('Effective LIVE request (the launcher itself sends nothing):')
    print(f"  endpoint: {request['endpoint']} | profile: {request['payload_profile']} | "
          f"max_tokens: {request['max_tokens']} | thinking: {request['chat_template_kwargs']}")
    if request['endpoint'] != 'eu-north1' or request['payload_profile'] != 'minimal_no_thinking':
        env.pop('NEBIUS_API_KEY', None)
        print('STOP: the effective request is not the verified profile. Nothing launched.')
        return 3
    print('Launching local Streamlit. Default execution is still OFFLINE replay.')
    print('Live option must be explicitly selected. Stop with Ctrl+C.')
    try:
        return subprocess.call([sys.executable, '-m', 'streamlit', 'run', str(root/'streamlit_app.py'),
                                '--server.address', '127.0.0.1'], cwd=root, env=env)
    except KeyboardInterrupt:
        return 130
    finally:
        env.pop('NEBIUS_API_KEY', None)


if __name__ == '__main__':
    raise SystemExit(main())
