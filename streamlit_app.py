from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from html import escape
import json
import os

import boto3
import streamlit as st

from app.actions import execute_actions
from app.agent import route_case
from app.nebius_agent import NebiusRouteError, route_case_nebius
from app.dynamo_state import DynamoWorkflowStateStore
from app.review_integration import register_review, review_store, scripted_synthetic_replay
from app.review_ui import render_review_center
from app.workspace_state import (select_review_case, status_display, escalation_display,
    reviews_for_scenario, review_history_reason, historical_review_warning, WORKSPACE_VIEWS,
    current_attempt_summary, saved_review_label, execution_mode_options, classify_live_failure,
    current_result_is_displayable, workflow_trace, next_action, observation_source_mode,
    ROUTINE_ACTION_LABEL, ROUTINE_STATUS_LABEL, action_display_note, workflow_status_label,
    sidebar_nebius_note, public_demo_enabled, assert_mode_allowed, workspace_data_root,
    PUBLIC_DEMO_NOTE, PUBLIC_SIDEBAR_NOTE, PUBLIC_FOOTER_NOTE, gate_effect)
from app.synthetic_profiles import (
    SyntheticPatientStore, case_with_demo_profile, review_predates_demo_context_edit,
)


# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="CareCanopy",
    page_icon="🌿",
    layout="wide",
)


# =========================================================
# DESIGN SYSTEM
# =========================================================

st.markdown(
    dedent("""
    <style>
        :root {
            --cc-ink: #172321;
            --cc-muted: #66746F;
            --cc-line: #DDE5E1;
            --cc-surface: #FFFFFF;
            --cc-canvas: #F5F8F6;
            --cc-sidebar: #EEF3F0;
            --cc-green: #176B4D;
            --cc-green-dark: #12543D;
            --cc-green-soft: #EAF5EF;
            --cc-amber: #8A5A00;
            --cc-amber-soft: #FFF4D8;
            --cc-red: #B42318;
            --cc-red-soft: #FDECEC;
            --cc-slate-soft: #EEF2F3;
        }

        .stApp {
            background:
                linear-gradient(180deg, #F9FBFA 0px, #F5F8F6 260px, #F5F8F6 100%);
            color: var(--cc-ink);
        }

        .block-container {
            max-width: 1220px;
            padding-top: 1.25rem;
            padding-bottom: 3rem;
        }

        /* Reduce default Streamlit chrome without changing app logic. */
        #MainMenu,
        footer,
        [data-testid="stAppDeployButton"] {
            display: none !important;
        }

        header[data-testid="stHeader"] {
            background: transparent;
            box-shadow: none;
        }

        section[data-testid="stSidebar"] {
            background: var(--cc-sidebar);
            border-right: 1px solid var(--cc-line);
        }

        section[data-testid="stSidebar"] > div {
            padding-top: 1.2rem;
        }

        .product-shell {
            background: rgba(255, 255, 255, 0.94);
            border: 1px solid var(--cc-line);
            border-radius: 20px;
            padding: 1.35rem 1.55rem 1.2rem 1.55rem;
            margin-bottom: 1rem;
            box-shadow: 0 8px 28px rgba(23, 107, 77, 0.06);
        }

        .product-eyebrow {
            color: var(--cc-green);
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            margin-bottom: 0.35rem;
        }

        .product-title-row {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: flex-start;
        }

        .care-title {
            color: var(--cc-ink);
            font-size: 2.35rem;
            font-weight: 820;
            letter-spacing: -0.045em;
            line-height: 1.05;
            margin: 0;
        }

        .care-subtitle {
            color: var(--cc-muted);
            font-size: 0.98rem;
            line-height: 1.55;
            margin-top: 0.3rem;
        }

        .tagline {
            color: #34433E;
            font-weight: 720;
        }

        .principle-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.95rem;
        }

        .principle-chip {
            display: inline-flex;
            align-items: center;
            gap: 0.3rem;
            background: #F7FAF8;
            color: #40514B;
            border: 1px solid var(--cc-line);
            border-radius: 999px;
            padding: 0.34rem 0.62rem;
            font-size: 0.78rem;
            font-weight: 650;
        }

        .workflow-strip {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 0.55rem;
            margin-top: 0.85rem;
        }

        .workflow-step {
            background: #FAFCFB;
            border: 1px solid var(--cc-line);
            border-radius: 11px;
            padding: 0.58rem 0.7rem;
            color: #5A6863;
            font-size: 0.76rem;
            line-height: 1.25;
        }

        .workflow-step b {
            color: var(--cc-ink);
            display: block;
            font-size: 0.79rem;
            margin-bottom: 0.12rem;
        }

        .sidebar-brand {
            color: var(--cc-ink);
            font-size: 1.04rem;
            font-weight: 800;
            margin-bottom: 0.15rem;
        }

        .sidebar-copy {
            color: var(--cc-muted);
            font-size: 0.78rem;
            line-height: 1.45;
            margin-bottom: 0.7rem;
        }

        .demo-badge {
            display: inline-block;
            color: #596762;
            background: rgba(255,255,255,0.7);
            border: 1px solid var(--cc-line);
            border-radius: 999px;
            padding: 0.26rem 0.5rem;
            font-size: 0.7rem;
            font-weight: 700;
            margin: 0.45rem 0 0.75rem 0;
        }

        .background-task {
            background: var(--cc-green-soft);
            border: 1px solid #B7D9C7;
            border-radius: 14px;
            padding: 0.85rem 1rem;
            margin: 0.55rem 0 1.15rem 0;
            box-shadow: 0 4px 16px rgba(23, 107, 77, 0.035);
        }

        .background-task-title {
            color: var(--cc-green);
            font-size: 0.95rem;
            font-weight: 800;
            margin-bottom: 0.22rem;
        }

        .background-task-main {
            color: var(--cc-ink);
            font-size: 0.98rem;
            font-weight: 700;
            margin-bottom: 0.2rem;
        }

        .background-task-meta {
            color: #5F6E68;
            font-size: 0.82rem;
            line-height: 1.45;
        }

        .hero-urgent {
            background: #FFF8F6;
            border: 1px solid #F0B7B1;
            border-left: 4px solid var(--cc-red);
            border-radius: 14px;
            padding: 0.95rem 1.05rem;
            margin: 0.9rem 0;
        }

        .hero-title {
            font-size: 0.98rem;
            font-weight: 800;
            color: var(--cc-red);
            margin-bottom: 0.3rem;
        }

        .hero-text {
            color: #56645F;
            line-height: 1.48;
            font-size: 0.86rem;
        }

        /* Make default Streamlit controls feel closer to a service workspace. */
        div[data-baseweb="select"] > div {
            background: white;
            border-color: #CFDAD5;
            border-radius: 11px;
            min-height: 2.7rem;
        }

        .stTextArea textarea {
            background: white !important;
            border: 1px solid #CFDAD5 !important;
            border-radius: 13px !important;
            box-shadow: none !important;
            line-height: 1.5;
        }

        .stTextArea textarea:focus {
            border-color: #6EAA8E !important;
            box-shadow: 0 0 0 2px rgba(23, 107, 77, 0.08) !important;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--cc-line);
            border-radius: 14px;
            background: rgba(255,255,255,0.82);
        }

        div[data-testid="stMetric"] {
            background: white;
            border: 1px solid var(--cc-line);
            padding: 0.75rem 0.9rem;
            border-radius: 12px;
            box-shadow: none;
        }

        div[data-testid="stMetricValue"] {
            font-size: 1.16rem;
        }

        div.stButton > button {
            border-radius: 11px;
            min-height: 2.9rem;
            font-weight: 750;
            background-color: var(--cc-green);
            border-color: var(--cc-green);
            color: white;
            box-shadow: 0 4px 14px rgba(23, 107, 77, 0.12);
        }

        div.stButton > button:hover {
            background-color: var(--cc-green-dark);
            border-color: var(--cc-green-dark);
            color: white;
        }

        hr {
            border-color: var(--cc-line) !important;
        }

        .cc-compact-header { padding: 0.6rem 1.1rem; margin-bottom: 0.4rem; display: flex; flex-wrap: wrap;
            align-items: center; justify-content: space-between; gap: 0.35rem 1rem; }
        .cc-compact-header .care-title { font-size: 1.5rem; }
        .cc-compact-header .care-subtitle { font-size: 0.82rem; margin-top: 0.1rem; }
        .cc-journey { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0; }
        /* Active workspace tab: CareCanopy green, not Streamlit's default red. */
        [data-testid="stButtonGroup"] button[aria-checked="true"] {
            background: var(--cc-green-soft) !important; color: var(--cc-green-dark) !important;
            border-color: var(--cc-green) !important; font-weight: 750; }
        .cc-attempt { display: flex; flex-wrap: wrap; gap: 0.2rem 0.6rem; align-items: baseline;
            padding: 0.45rem 0.8rem; border: 1px solid var(--cc-line); border-radius: 12px;
            background: rgba(255,255,255,0.82); font-size: 0.84rem; color: var(--cc-ink); }
        .cc-attempt-label { font-size: 0.72rem; font-weight: 800; letter-spacing: 0.04em; color: var(--cc-muted); }
        .cc-attempt-note { color: var(--cc-muted); font-size: 0.8rem; }
        .cc-journey span { display: inline-flex; align-items: center; border: 1px solid #D5E3DA;
            background: #F3F8F5; padding: 0.22rem 0.55rem; border-radius: 999px;
            color: #285641; font-weight: 700; font-size: 0.73rem; }
        .cc-journey span + span::before { content: "→"; color: #61796D; margin-right: 0.4rem; }
        /* Patient & AI numbered workflow cards */
        .cc-step-head { display: flex; align-items: flex-start; gap: 0.7rem; }
        .cc-step-num { flex: 0 0 auto; width: 1.85rem; height: 1.85rem; border-radius: 999px;
            background: var(--cc-green); color: white; font-weight: 800; font-size: 0.95rem;
            display: inline-flex; align-items: center; justify-content: center; }
        .cc-step-title { font-size: 1.08rem; font-weight: 800; color: var(--cc-ink); line-height: 1.3; }
        .cc-step-sub { font-size: 0.82rem; color: var(--cc-muted); margin-top: 0.1rem; }
        .cc-kicker { font-size: 0.78rem; font-weight: 750; color: #3C5248; margin: 0.15rem 0 0.35rem; }
        .cc-snapshot { width: 100%; border-collapse: collapse; font-size: 0.86rem;
            background: white; border: 1px solid var(--cc-line); border-radius: 10px; overflow: hidden; }
        .cc-snapshot th, .cc-snapshot td { text-align: left; padding: 0.3rem 0.65rem;
            border-bottom: 1px solid var(--cc-line); vertical-align: top; }
        .cc-snapshot tr:last-child th, .cc-snapshot tr:last-child td { border-bottom: none; }
        .cc-snapshot th { width: 42%; font-weight: 700; color: var(--cc-ink); background: #FAFCFB; }
        .cc-flow-wrap { container-type: inline-size; }
        .cc-flow { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.55rem; margin-top: 0.35rem; }
        @container (min-width: 860px) { .cc-flow { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
        @container (max-width: 460px) { .cc-flow { grid-template-columns: 1fr; } }
        .cc-flow-card { padding: 0.7rem 0.8rem; background: white; border: 1px solid var(--cc-line);
            border-radius: 12px; min-width: 0; }
        .cc-flow-card.tone-critical { border-color: #E9B4AE; }
        .cc-flow-card.tone-caution { border-color: #EBCF8E; }
        .cc-flow-n { font-size: 0.7rem; font-weight: 800; color: var(--cc-muted); }
        .cc-flow-title { font-size: 0.86rem; font-weight: 800; color: var(--cc-ink); margin: 0.1rem 0 0.4rem; }
        .cc-flow-note { font-size: 0.74rem; color: var(--cc-muted); margin-top: 0.38rem; overflow-wrap: anywhere; }
        .cc-chip { display: inline-block; padding: 0.16rem 0.55rem; border-radius: 999px; font-size: 0.74rem;
            font-weight: 750; border: 1px solid transparent; overflow-wrap: anywhere; }
        .cc-chip.tone-pending, .cc-chip.tone-skipped { background: var(--cc-slate-soft); color: #4A5A55; border-color: #D7DFDC; }
        .cc-chip.tone-neutral { background: #EEF4F8; color: #2D4A5E; border-color: #D3E0EA; }
        .cc-chip.tone-ok { background: var(--cc-green-soft); color: var(--cc-green-dark); border-color: #C9E3D5; }
        .cc-chip.tone-caution { background: var(--cc-amber-soft); color: var(--cc-amber); border-color: #EBCF8E; }
        .cc-chip.tone-critical { background: var(--cc-red-soft); color: var(--cc-red); border-color: #E9B4AE; }
        .cc-mode-note { font-size: 0.8rem; color: var(--cc-muted); }
        /* Card 4 decision workspace */
        .cc-chip-right { display: flex; justify-content: flex-end; }
        .cc-panel-kicker { font-size: 0.7rem; font-weight: 800; letter-spacing: 0.07em;
            text-transform: uppercase; color: var(--cc-muted); }
        .cc-route-panel { height: 100%; box-sizing: border-box; background: white;
            border: 1px solid var(--cc-line); border-left: 4px solid #B9C6C0;
            border-radius: 14px; padding: 0.85rem 1rem; }
        .cc-route-panel.tone-ok { border-left-color: var(--cc-green); }
        .cc-route-panel.tone-caution { border-left-color: #C98A00; }
        .cc-route-panel.tone-critical { border-left-color: var(--cc-red); }
        .cc-route-big { font-size: 1.45rem; font-weight: 850; letter-spacing: 0.02em;
            line-height: 1.15; margin: 0.2rem 0 0.15rem; color: var(--cc-ink); }
        .cc-route-panel.tone-ok .cc-route-big { color: var(--cc-green-dark); }
        .cc-route-panel.tone-caution .cc-route-big { color: var(--cc-amber); }
        .cc-route-panel.tone-critical .cc-route-big { color: var(--cc-red); }
        .cc-route-title { font-size: 0.92rem; font-weight: 750; color: var(--cc-ink); }
        .cc-route-note { font-size: 0.8rem; line-height: 1.45; color: var(--cc-muted); margin-top: 0.25rem; }
        .cc-route-meta { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-top: 0.6rem; }
        .st-key-cc_next_action { background: var(--cc-green-soft); border: 1px solid #C9E3D5;
            border-radius: 14px; padding: 0.85rem 1rem; height: 100%; box-sizing: border-box; gap: 0.45rem; }
        /* Let the Next Action panel match the Final Route panel's height. */
        [data-testid="stLayoutWrapper"]:has(> .st-key-cc_next_action) { flex: 1 1 auto; }
        .cc-next-text { font-size: 0.9rem; line-height: 1.45; color: var(--cc-ink); margin-top: 0.25rem; }
        .cc-footnote { font-size: 0.72rem; color: #4F6B5D; }
        .cc-why { margin-top: 0.15rem; padding: 0.7rem 0.9rem; background: #FAFCFB;
            border: 1px solid var(--cc-line); border-radius: 12px; }
        .cc-why-text { font-size: 0.85rem; line-height: 1.5; color: var(--cc-ink); margin-top: 0.2rem; }
        .cc-why-label { font-size: 0.74rem; font-weight: 750; color: #3C5248; margin-top: 0.5rem; }
        .cc-why ul { margin: 0.2rem 0 0 1.1rem; padding: 0; font-size: 0.83rem; color: var(--cc-ink); }
        /* Details & local records (inside one expander; tabs, not expanders) */
        .cc-rows { border: 1px solid var(--cc-line); border-radius: 12px; background: white; overflow: hidden; }
        .cc-row { display: flex; flex-wrap: wrap; gap: 0.15rem 1rem; justify-content: space-between;
            padding: 0.55rem 0.8rem; border-bottom: 1px solid var(--cc-line); }
        .cc-row:last-child { border-bottom: none; }
        .cc-row-title { font-size: 0.84rem; font-weight: 750; color: var(--cc-ink); }
        .cc-row-note { font-size: 0.8rem; color: var(--cc-muted); flex: 1 1 320px; text-align: right; }
        .cc-kv { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 0.5rem;
            margin-bottom: 0.5rem; }
        .cc-kv > div { background: white; border: 1px solid var(--cc-line); border-radius: 10px;
            padding: 0.5rem 0.75rem; }
        .cc-kv-label { font-size: 0.72rem; font-weight: 700; color: var(--cc-muted); }
        .cc-kv-value { font-size: 0.88rem; font-weight: 750; color: var(--cc-ink); margin-top: 0.1rem; }
        /* Details tabs: CareCanopy green, not Streamlit's default red (red = escalation only). */
        [data-testid="stExpander"] [data-testid="stTab"][aria-selected="true"],
        [data-testid="stExpander"] [data-testid="stTab"][aria-selected="true"] p {
            color: var(--cc-green-dark) !important; }
        [data-testid="stExpander"] .react-aria-SelectionIndicator { background-color: var(--cc-green) !important; }
        @media (max-width: 700px) { .cc-row-note { text-align: left; } }
        /* Clinical Review / Outcomes & Audit */
        .cc-page-head { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
            gap: 0.5rem 1rem; margin: 0.2rem 0 0.4rem; }
        .cc-page-title { font-size: 1.45rem; font-weight: 800; color: var(--cc-ink); }
        .cc-outcome-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.6rem;
            margin: 0.5rem 0 0.7rem; }
        @media (max-width: 900px) { .cc-outcome-cards { grid-template-columns: 1fr; } }
        .cc-outcome-card { background: white; border: 1px solid var(--cc-line); border-radius: 14px;
            padding: 0.75rem 0.9rem; min-width: 0; }
        .cc-outcome-value { font-size: 1.05rem; font-weight: 800; margin: 0.25rem 0 0.15rem; color: var(--cc-ink); }
        .cc-outcome-value.tone-ok { color: var(--cc-green-dark); }
        .cc-outcome-value.tone-caution { color: var(--cc-amber); }
        .cc-outcome-value.tone-critical { color: var(--cc-red); }
        .cc-outcome-value.tone-neutral { color: #4A5A55; }
        .cc-timeline { display: flex; flex-direction: column; gap: 0.55rem; margin-top: 0.4rem; }
        .cc-tl-item { display: flex; gap: 0.7rem; align-items: flex-start; }
        .cc-tl-num { flex: 0 0 auto; min-width: 2rem; height: 2rem; border-radius: 999px; padding: 0 0.3rem;
            border: 2px solid #9CCBB4; color: var(--cc-green-dark); font-weight: 800; font-size: 0.85rem;
            display: inline-flex; align-items: center; justify-content: center; background: white; }
        .cc-tl-body { flex: 1 1 auto; min-width: 0; background: white; border: 1px solid var(--cc-line);
            border-radius: 12px; padding: 0.55rem 0.75rem; }
        .cc-tl-top { display: flex; justify-content: space-between; gap: 0.5rem; align-items: center;
            font-size: 0.88rem; color: var(--cc-ink); }
        .cc-tl-meta { font-size: 0.74rem; color: var(--cc-muted); margin-top: 0.15rem; overflow-wrap: anywhere; }

        @media (max-width: 900px) {
            .workflow-strip {
                grid-template-columns: repeat(2, 1fr);
            }

            .product-title-row {
                display: block;
            }
        }
    </style>
    """),
    unsafe_allow_html=True,
)


# =========================================================
# CONSTANTS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
DEV_CASES_PATH = BASE_DIR / "data" / "dev_cases.json"

AWS_REGION = "ap-northeast-2"
DYNAMO_TABLE = "CareCanopyWorkflowStates"


ACTION_LABELS = {
    "COMPLETE_ROUTINE_FOLLOWUP":
        ROUTINE_ACTION_LABEL,  # Display only; backend action name unchanged.

    "REQUEST_CLARIFICATION":
        "Request clarification",

    "ISSUE_IMMEDIATE_MEDICAL_GUIDANCE":
        "Immediate medical pathway",

    "NOTIFY_CLINICAL_TEAM":
        "Record clinical-team notification action",

    "CREATE_REVIEW_PACKET":
        "Create human review packet",
}


ESCALATION_LABELS = {
    "URGENT_MEDICAL":
        "Urgent medical",

    "NON_URGENT_PROFESSIONAL":
        "Professional review",

    "SCOPE_BOUNDARY":
        "Scope boundary",
}


STATUS_LABELS = {
    "ROUTINE_COMPLETED":
        ROUTINE_STATUS_LABEL,  # Display only; never implies care was delivered.

    "AWAITING_CLARIFICATION":
        "Awaiting clarification",

    "ESCALATED":
        "Escalated",
}


CONTEXT_LABELS = {
    "age":
        "Age",

    "weeks_post_stroke":
        "Time since stroke",

    "baseline":
        "Baseline",

    "approved_plan":
        "Specialist-approved plan",
}


# =========================================================
# DATA HELPERS
# =========================================================

@st.cache_data
def load_dev_cases() -> list[dict]:
    with open(
        DEV_CASES_PATH,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


@st.cache_data(ttl=15)
def load_proactive_tasks() -> list[dict]:
    """
    Read proactive follow-up tasks created by
    Amazon EventBridge Scheduler.
    """

    dynamodb = boto3.resource(
        "dynamodb",
        region_name=AWS_REGION,
    )

    table = dynamodb.Table(
        DYNAMO_TABLE
    )

    response = table.scan(
        FilterExpression=(
            "begins_with(case_id, :prefix)"
        ),
        ExpressionAttributeValues={
            ":prefix": "PROACTIVE_DEMO_"
        },
    )

    tasks = response.get(
        "Items",
        [],
    )

    tasks.sort(
        key=lambda item: item.get(
            "updated_at",
            "",
        ),
        reverse=True,
    )

    return tasks


def reset_demo_state():
    keys_to_clear = [
        "carecanopy_result",
        "workflow_case_id",
        "clarification_round",
        "base_report",
        "last_clarification",
        "cc_v2_current_attempt",
    ]

    for key in keys_to_clear:
        st.session_state.pop(
            key,
            None,
        )


def escalation_label(decision) -> str:
    if decision.escalation_type is None:
        return "—"

    value = decision.escalation_type.value

    return ESCALATION_LABELS.get(
        value,
        value,
    )


def run_pipeline(case: dict, offline_replay: bool, *, nebius_live: bool = False) -> dict:
    """Exactly one selected provider. Offline replay remains the safe default.

    Live Nebius performs one model call and deterministic safety gating; action
    descriptions are local records, NOT dispatched to a clinician or patient.
    AWS v1 integration remains untouched for regression comparison.
    """
    # Public demo: block live providers on the execution path, not only in the UI.
    assert_mode_allowed(os.environ, offline_replay=offline_replay, nebius_live=nebius_live)
    if nebius_live:
        decision, provenance = route_case_nebius(case)
        action = execute_actions(case, decision)
        # Legacy action names describe *intended* tasks. Do not record actual
        # routine clinical care as completed merely because Nemotron proposed it.
        if decision.route.value == 'ROUTINE':
            action['status'] = 'ROUTINE_RECOMMENDED_INTERNAL'
            for item in action.get('actions', []):
                if item.get('action') == 'COMPLETE_ROUTINE_FOLLOWUP':
                    item['description'] = (
                        'Routine within-plan follow-up recommended and recorded locally; '
                        'no external task completion has been verified.')
        action_names = [entry.get('action') for entry in action.get('actions', [])]
        if isinstance(action.get('review_packet'), dict):
            # Persist sanitized model evidence with review packet for audit.
            action['review_packet']['model_provenance'] = provenance.public_record()
        state = {
            'case_id': case['id'], 'status': action['status'],
            'routine_followup_completed': False,  # Clinical care not performed by model.
            'clarification_requested': 'REQUEST_CLARIFICATION' in action_names,
            'review_required': 'CREATE_REVIEW_PACKET' in action_names,
            'clinical_team_notified': False,  # No external delivery configured.
            'audit_log': [{'timestamp': action['executed_at'], 'action': name,
                           'source': 'LIVE NEMOTRON PROPOSAL; LOCAL ACTION RECORD ONLY'}
                          for name in action_names],
        }
        result = {'decision': decision, 'actions': action, 'state': state,
                  'mode': 'NEBIUS_NEMOTRON_LIVE',
                  'model_provenance': provenance.public_record()}
    elif offline_replay:
        result = scripted_synthetic_replay(case)
    else:
        decision = route_case(case)
        action_result = execute_actions(case, decision)
        store = DynamoWorkflowStateStore()
        state = store.apply_actions(case, action_result)
        result = {'decision': decision, 'actions': action_result,
                  'state': state, 'mode': 'AWS_BEDROCK_V1'}
    review = register_review(result, case, review_store(DATA_ROOT))
    result['local_review_case_id'] = review['case_id'] if review else None
    return result


# =========================================================
# HEADER
# =========================================================

st.html(dedent("""
<div class="product-shell cc-compact-header">
    <div>
      <div class="care-title">CareCanopy</div>
      <div class="care-subtitle">Safe follow-up routing with independent checks and human review · Synthetic workspace</div>
    </div>
    <div class="cc-journey" aria-label="CareCanopy workflow">
      <span>Observe</span><span>AI proposal</span><span>Validate</span>
      <span>Safety Gate</span><span>Review / handoff</span><span>Audit</span>
    </div>
</div>
"""))

# =========================================================
# EXECUTION MODE: isolated scripted UI replay until Nebius access is ready
# =========================================================

# Live mode stays invisible until explicitly enabled *and* a local key exists.
# Never expose paid model calls merely because someone opens a public demo.
mode_options, nebius_available = execution_mode_options(os.environ)
execution_mode = st.sidebar.radio(
    'Execution mode', options=mode_options, index=0, on_change=reset_demo_state,
    help=('Default mode uses predefined synthetic labels and NO AI. '
          'Live Nebius requires explicit environment configuration and may incur costs. '
          'AWS Bedrock (v1) is an optional legacy/comparison mode. '
          'No external clinical notifications are sent.'),
)
public_demo = public_demo_enabled(os.environ)
# Normal app: project folder (unchanged). Public demo: per-session temporary folder.
DATA_ROOT = workspace_data_root(os.environ, BASE_DIR, st.session_state)
if not nebius_available:
    # Non-secret status only; never echo environment values.
    st.sidebar.caption('Nebius LIVE: Disabled in public demo' if public_demo
                       else 'Nebius LIVE: Not configured')
is_offline_replay = execution_mode.startswith('Offline')
is_nebius_live = execution_mode.startswith('Live Nebius')
if public_demo:
    # One banner in the public demo; it already states OFFLINE · NO AI.
    st.info(PUBLIC_DEMO_NOTE)
elif is_offline_replay:
    st.caption('**OFFLINE REPLAY · NO AI** — Scripted synthetic labels; local SQLite only. Not evidence of model performance.')
elif is_nebius_live:
    st.caption('**LIVE NEBIUS · PAID API** — One opt-in model request per Analyze; local simulated review only. No external delivery.')
else:
    st.caption('**AWS BEDROCK V1 · OPTIONAL** — DynamoDB workflow; separate local SQLite review prototype.')

# =========================================================
# PROACTIVE BACKGROUND TASK
# =========================================================

if is_offline_replay or is_nebius_live:
    proactive_tasks = []
else:
    try:
        proactive_tasks = load_proactive_tasks()
    except Exception:
        proactive_tasks = []


if proactive_tasks:

    latest_task = proactive_tasks[0]

    reason = latest_task.get(
        "reason",
        "Scheduled follow-up is overdue.",
    )

    source = latest_task.get(
        "task_source",
        "Amazon EventBridge Scheduler",
    )

    task_count = len(proactive_tasks)
    task_word = "task" if task_count == 1 else "tasks"

    background_html = (
        '<div class="background-task">'
        '<div class="background-task-title">'
        '⚡ Background task detected'
        '</div>'
        '<div class="background-task-main">'
        f'{task_count} overdue rehabilitation follow-up '
        f'{task_word} detected'
        '</div>'
        '<div class="background-task-meta">'
        f'Latest task: {reason}'
        '<br>'
        f'Created automatically by {source}. '
        'The overdue follow-up is surfaced before the worker '
        'has to create the task manually.'
        '</div>'
        '</div>'
    )

    st.markdown(
        background_html,
        unsafe_allow_html=True,
    )

elif is_offline_replay or is_nebius_live:
    pass  # Compact local workspace: no irrelevant AWS background-task notice.

else:

    background_html = (
        '<div class="background-task">'
        '<div class="background-task-title">'
        'Proactive follow-up workflow active'
        '</div>'
        '<div class="background-task-meta">'
        'Amazon EventBridge Scheduler checks scheduled follow-ups '
        'and can create overdue tasks.'
        '</div>'
        '</div>'
    )

    st.markdown(
        background_html,
        unsafe_allow_html=True,
    )


# =========================================================
# SIDEBAR
# =========================================================

cases = load_dev_cases()

with st.sidebar:

    st.markdown(
        '<div class="sidebar-brand">Follow-up Queue</div>'
        '<div class="sidebar-copy">Select a synthetic follow-up scenario for the demo workspace.</div>',
        unsafe_allow_html=True,
    )

    case_index = st.selectbox(
        "Select follow-up",
        options=range(
            len(cases)
        ),
        format_func=lambda index: (
            f"{cases[index]['id']} — "
            f"{cases[index]['title']}"
        ),
        key="case_selector",
        on_change=reset_demo_state,
    )

    selected_case = (
        cases[case_index]
    )

    st.markdown(
        '<span class="demo-badge">Demo data · Synthetic</span>',
        unsafe_allow_html=True,
    )

    st.caption(
        "The original frozen evaluation benchmark is unchanged."
    )

    st.divider()

    st.caption(
        PUBLIC_SIDEBAR_NOTE if public_demo else
        'Offline replay: fixtures + local SQLite. '
        'AWS v1 (optional legacy/comparison): Bedrock / DynamoDB / EventBridge. '
        + sidebar_nebius_note(
            nebius_available, is_nebius_live,
            current_attempt_summary(st.session_state.get('cc_v2_current_attempt'),
                                    selected_case['id'], execution_mode)[2])
    )


# =========================================================
# PERSISTENT WORKSPACE NAVIGATION
# =========================================================

# Streamlit's segmented control uses session state to preserve the selected
# workspace across reruns. Patient input drafts use separate session keys;
# saved reviews remain in the SQLite database across process restarts.
service = review_store(DATA_ROOT)
# The sidebar scenario and its local review history must always refer to
# the same synthetic patient. Never display DEV04's completed status over
# DEV01's patient snapshot, even after a process restart.
# Keep only synthetic DEV## records; do not store names, addresses or contacts.
patient_store = SyntheticPatientStore(DATA_ROOT / '.carecanopy_local' / 'synthetic_patients.sqlite3')
selected_profile = patient_store.seed_from_fixture(selected_case)
patient_id = selected_profile['patient_id']
active_case = case_with_demo_profile(selected_case, selected_profile)

saved_reviews = reviews_for_scenario(service.list_reviews(), selected_case['id'])
saved_review_ids = [item['case_id'] for item in saved_reviews]

# A selection change invalidates the old scenario's review picker, but does
# not alter persisted review records. They remain available on returning.
if st.session_state.get('cc_v2_active_scenario_id') != selected_case['id']:
    st.session_state.pop('cc_v2_review_picker', None)
    st.session_state['cc_v2_active_scenario_id'] = selected_case['id']

preferred_review_id = (st.session_state.get('carecanopy_result') or {}).get('local_review_case_id')

# A fresh routing result takes focus only if it belongs to this scenario.
if (preferred_review_id in saved_review_ids and
        st.session_state.get('cc_v2_last_preferred_review') != preferred_review_id):
    st.session_state['cc_v2_review_picker'] = preferred_review_id
    st.session_state['cc_v2_last_preferred_review'] = preferred_review_id

chosen_review_id = select_review_case(
    saved_review_ids,
    preferred_id=None,
    prior_id=st.session_state.get('cc_v2_review_picker'),
)

# Seed the tab via Session State only (no `default=`): the review hand-off
# callback also sets this key, and Streamlit warns when both are used.
st.session_state.setdefault('cc_v2_workspace_view', 'Patient & AI')
active_view = st.segmented_control(
    'Workspace section', WORKSPACE_VIEWS,
    key='cc_v2_workspace_view',
    selection_mode='single', label_visibility='collapsed',
) or 'Patient & AI'

# CURRENT RUN: session-scoped, never inferred from an old SQLite review.
current_attempt = st.session_state.get('cc_v2_current_attempt')
run_title, run_note, run_state = current_attempt_summary(
    current_attempt, selected_case['id'], execution_mode)
attempt_label = 'CURRENT ATTEMPT · ' + ('Live Nemotron' if is_nebius_live else
                'Offline scripted replay' if is_offline_replay else 'AWS v1')
if run_state == 'blocked':
    st.warning('**' + attempt_label + ' — ' + run_title + '** — ' + run_note)
else:
    # One compact row; text comes from fixed summaries, escaped regardless.
    st.html('<div class="cc-attempt"><span class="cc-attempt-label">' + escape(attempt_label) +
            '</span><strong>' + escape(run_title) + '</strong><span class="cc-attempt-note">' +
            escape(run_note if run_state == 'routed' else
                   'No new routing result has been verified for this execution mode.') +
            '</span></div>')

# Saved reviews are persistent historical records, not the current LIVE attempt.
if saved_review_ids:
    review_lookup = {r['case_id']: r for r in saved_reviews}
    with st.expander(f'Review history ({len(saved_review_ids)}) · Saved local records', expanded=False):
        st.caption('Earlier records may remain valid for simulated review in the same verified context, '
                   'but are NOT results from this current analysis attempt.')
        review_default_index = saved_review_ids.index(chosen_review_id)
        chosen_review_id = st.selectbox(
            'Select a saved review', saved_review_ids, index=review_default_index,
            format_func=lambda review_id: saved_review_label(
                review_lookup[review_id],
                historical=bool(review_history_reason(
                    selected_profile, review_lookup[review_id],
                    service.review_context_state(review_id)))),
            key='cc_v2_review_picker',
            help='Select a historical SQLite review; no model is called.',
        )
        st.caption('Immutable review ID: `' + chosen_review_id + '`')
        selected_review = service.get(chosen_review_id)
        if selected_review:
            history_reason = review_history_reason(
                selected_profile, selected_review,
                service.review_context_state(chosen_review_id))
            st.caption('Saved review status: ' + status_display(
                selected_review['status'], historical=bool(history_reason),
                reason=history_reason or 'changed')[0] +
                ' · ' + escalation_display(selected_review['escalation_type']))
        st.caption('Internal handoff records are in-app only; no external delivery or clinical completion verified.')
else:
    st.caption('Review history: no saved local review for this synthetic scenario.')

# A stale context warning remains prominent even if the history picker is collapsed.
if chosen_review_id:
    _saved = service.get(chosen_review_id)
    if _saved:
        _reason = review_history_reason(
            selected_profile, _saved, service.review_context_state(chosen_review_id))
        if _reason:
            st.warning(historical_review_warning(_reason))
        elif run_state != 'routed' and is_nebius_live:
            st.caption('A previous local review exists; it is not a successful result of the current LIVE attempt.')


def _capture_report_draft(case_id: str, widget_key: str) -> None:
    st.session_state[f'cc_v2_report_draft_{case_id}'] = st.session_state.get(widget_key, '')


def step_header(number: int, title: str, subtitle: str) -> None:
    st.html('<div class="cc-step-head"><span class="cc-step-num">' + str(int(number)) +
            '</span><div><div class="cc-step-title">' + escape(title) +
            '</div><div class="cc-step-sub">' + escape(subtitle) + '</div></div></div>')


if active_view == "Patient & AI":
    # =========================================================
    # ① PATIENT CONTEXT + FRONTLINE OBSERVATION
    # =========================================================

    recent_reports = patient_store.observations(patient_id, limit=5)
    with st.container(border=True):
      head_col, profile_col, history_col = st.columns([2.3, 1.15, 1], vertical_alignment='center')
      with head_col:
        step_header(1, 'Patient context + Frontline observation',
                    'Synthetic patient ' + patient_id + ' · Demo data only. '
                    'Never enter real patient information.')
      with profile_col:
       with st.popover('Edit synthetic patient profile', use_container_width=True):
            st.caption('These fields are synthetic and unverified. The specialist-approved plan is locked in this demo.')
            with st.form('synthetic_profile_form_' + selected_case['id']):
                col_age, col_weeks = st.columns(2)
                with col_age:
                    demo_age = st.number_input('Age (years)', min_value=18, max_value=120,
                                               value=int(selected_profile['age']), step=1)
                with col_weeks:
                    demo_weeks = st.number_input('Weeks since stroke', min_value=0, max_value=520,
                                                 value=int(selected_profile['weeks_post_stroke']), step=1)
                demo_baseline = st.text_area('Functional baseline (synthetic)',
                                             value=selected_profile['baseline'], max_chars=600)
                st.text_input('Specialist-approved plan (locked demo fixture)',
                              value=selected_profile['approved_plan'], disabled=True)
                if st.form_submit_button('Save synthetic profile'):
                    patient_store.update_demo_context(patient_id, age=int(demo_age),
                        weeks_post_stroke=int(demo_weeks), baseline=demo_baseline)
                    reset_demo_state()  # An old routing result must not cover edited context.
                    st.rerun()
            st.caption('Offline replay uses predefined labels; editing this profile does not demonstrate a change in AI reasoning.')

      with history_col:
       with st.popover(f'Previous reports ({len(recent_reports)})', use_container_width=True):
            if not recent_reports:
                st.caption('No earlier reports saved for this synthetic patient.')
            for entry in recent_reports:
                st.caption(f"{entry['created_at']} · {entry['source_mode']} · {entry['case_id']}")
                st.write(entry['report'])

      left, right = st.columns([1, 1.25])

      with left:
        st.html('<div class="cc-kicker">Patient snapshot (synthetic)</div>')
        snapshot_rows = []
        for key, value in active_case["patient_context"].items():
            label = CONTEXT_LABELS.get(key, key.replace("_", " ").title())
            if key == "weeks_post_stroke":
                value = f"{value} weeks"
            # Synthetic profile fields are user-editable: always escape.
            snapshot_rows.append('<tr><th>' + escape(str(label)) + '</th><td>' +
                                 escape(str(value)) + '</td></tr>')
        st.html('<table class="cc-snapshot">' + ''.join(snapshot_rows) + '</table>')

      with right:
        st.html('<div class="cc-kicker">Frontline observation (today)</div>')
        current_report = (
            st.text_area(
                "Current follow-up report",
                value=st.session_state.get(
                    f"cc_v2_report_draft_{selected_case['id']}",
                    selected_case["current_report"],
                ),
                height=150,
                label_visibility="collapsed",
                key=(
                    f"report_"
                    f"{selected_case['id']}"
                ),
                on_change=_capture_report_draft,
                args=(selected_case['id'], f"report_{selected_case['id']}"),
            )
        )

    # =========================================================
    # ② RUN ANALYSIS
    # =========================================================

    if is_offline_replay:
        run_mode_note = 'Offline scripted replay · NO AI. No model is called; scripted fixture labels only.'
    elif is_nebius_live:
        run_mode_note = 'Live Nebius Nemotron · one paid API request per click. No external delivery.'
    else:
        run_mode_note = 'AWS Bedrock (v1) · optional legacy/comparison mode.'

    with st.container(border=True):
        run_head, run_cta = st.columns([2.3, 1.3], vertical_alignment='center')
        with run_head:
            step_header(2, 'Run analysis',
                        'Create a proposal and verify it with the independent Safety Gate.')
        with run_cta:
            run_button = st.button(
                "Analyze & route follow-up",
                type="primary",
                use_container_width=True,
            )
        st.html('<div class="cc-mode-note">' + escape(run_mode_note) + '</div>')
        st.caption('Only the current Analyze action can create a new routing result. '
                   'Saved records under Review history are not evidence of this attempt.')

    # =========================================================
    # INITIAL RUN
    # =========================================================

    if run_button:

        reset_demo_state()

        workflow_case_id = (
            f"{selected_case['id']}_UI_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
        )

        demo_case = case_with_demo_profile(
            selected_case, selected_profile,
            prior_observations=patient_store.observations(patient_id, limit=5),
        )

        demo_case["id"] = (
            workflow_case_id
        )

        demo_case["current_report"] = (
            current_report
        )

        demo_case["source_case_id"] = selected_case["id"]
        demo_case["synthetic_patient_id"] = "SYN-" + selected_case["id"]

        with st.spinner(
            "CareCanopy is reviewing "
            "the follow-up report..."
        ):

            if is_nebius_live:
                attempt_count = st.session_state.get('cc_v2_nebius_attempts', 0)
                if attempt_count >= 3:
                    st.error('Local demo call limit (3) reached in this browser session. '
                             'No additional live request was made.')
                    st.stop()
                st.session_state['cc_v2_nebius_attempts'] = attempt_count + 1
            try:
                result = run_pipeline(
                    demo_case, is_offline_replay, nebius_live=is_nebius_live)
            except NebiusRouteError as exc:
                # The exception text may carry provider diagnostics. Use it only
                # here to pick a fixed display phase; never store or show it.
                st.session_state['cc_v2_current_attempt'] = {
                    'scenario_id': selected_case['id'], 'execution_mode': execution_mode,
                    'outcome': 'blocked', 'phase': classify_live_failure(str(exc)),
                }
                st.rerun()
            # Store the raw source report only after the routing attempt returned.
            # This is NOT proof of a delivered message or completed clinical care.
            patient_store.save_observation(
                patient_id, case_id=workflow_case_id, report=current_report,
                source_mode=observation_source_mode(result['mode']),
            )

        st.session_state['cc_v2_current_attempt'] = {
            'scenario_id': selected_case['id'], 'execution_mode': execution_mode,
            'outcome': 'routed', 'route': result['decision'].route.value,
            'mode': result['mode'],
        }

        st.session_state[
            "carecanopy_result"
        ] = result

        st.session_state[
            "workflow_case_id"
        ] = workflow_case_id

        st.session_state[
            "clarification_round"
        ] = 0

        st.session_state[
            "base_report"
        ] = current_report

        st.session_state[
            "last_clarification"
        ] = None

        # Recompute the top status and saved review selector for this new case.
        st.rerun()


    # =========================================================
    # ③ ROUTING WORKFLOW (RESULTS)
    # =========================================================

    # Render a stored result ONLY as this attempt's verified, matching result:
    # never another scenario, another mode, or an earlier OFFLINE run as LIVE.
    show_result = current_result_is_displayable(
        current_attempt, st.session_state.get('carecanopy_result'), selected_case['id'],
        execution_mode, st.session_state.get('workflow_case_id'))
    if show_result:
        trace_steps = workflow_trace('routed', result=st.session_state['carecanopy_result'],
                                     scripted_label=selected_case.get('expected_route'),
                                     scripted_escalation=selected_case.get('expected_escalation_type'))
    elif run_state == 'blocked':
        trace_steps = workflow_trace('blocked', phase=current_attempt.get('phase'))
    else:
        trace_steps = workflow_trace('idle')

    with st.container(border=True):
        step_header(3, 'Routing workflow (results)',
                    'Proposal → Independent Safety Gate → Final route')
        st.html(
            '<div class="cc-flow-wrap"><div class="cc-flow">' + ''.join(
                '<div class="cc-flow-card tone-' + escape(step['tone']) + '">'
                '<div class="cc-flow-n">0' + str(number) + '</div>'
                '<div class="cc-flow-title">' + escape(step['title']) + '</div>'
                '<span class="cc-chip tone-' + escape(step['tone']) + '">' +
                escape(step['status']) + '</span>'
                '<div class="cc-flow-note">' + escape(step['note']) + '</div></div>'
                for number, step in enumerate(trace_steps, 1)
            ) + '</div></div>'
        )
        if show_result:
            st.caption('This trace does not prove external delivery, authenticated clinical review, '
                       'or clinical care completion. See Clinical Review and Outcomes for local records.')

    # =========================================================
    # ④ FINAL DECISION + NEXT ACTION (one card; rendered below)
    # =========================================================

    if show_result:

        result = st.session_state[
            "carecanopy_result"
        ]

        decision = result[
            "decision"
        ]

        action_result = result[
            "actions"
        ]

        state = result[
            "state"
        ]

        route = (
            decision.route.value
        )

        clarification_round = (
            st.session_state.get(
                "clarification_round",
                0,
            )
        )


    if is_offline_replay:
        card4_mode_chip = ('neutral', 'OFFLINE · NO AI')
    elif is_nebius_live:
        card4_mode_chip = ('neutral', 'LIVE · Nemotron')
    else:
        card4_mode_chip = ('neutral', 'AWS v1 · legacy')

    with st.container(border=True, key='cc_card4'):
        card4_head, card4_chip = st.columns([3.2, 1], vertical_alignment='center')
        with card4_head:
            step_header(4, 'Final decision + Next action',
                        'Verified result and the one next permissible step.')
        with card4_chip:
            st.html('<div class="cc-chip-right"><span class="cc-chip tone-' + card4_mode_chip[0] +
                    '">' + escape(card4_mode_chip[1]) + '</span></div>')

        if not show_result:
            if run_state == 'blocked':
                # Fixed, sanitized copy from current_attempt_summary(); no provider text.
                st.warning('**' + run_title + '** — ' + run_note)
            else:
                st.info('No current analysis for this case and execution mode yet. '
                        'Results appear here after you click "Analyze & route follow-up".')

        else:
            if result.get("mode") == "OFFLINE_SCRIPTED":
                st.caption("Scripted fixture result, not AI inference or measured model accuracy.")
            elif result.get('mode') == 'NEBIUS_NEMOTRON_LIVE':
                st.caption('LIVE Nebius Nemotron response received and checked by Safety Gate. '
                           'Review/handoff is locally recorded only; no message or clinical '
                           'action was delivered.')

            if clarification_round == 1:
                st.info(
                    "Re-routed after one clarification round. "
                    "The MVP permits only one clarification round "
                    "before unresolved uncertainty is handed to a human."
                )

            route_tone = {"ROUTINE": "ok", "CLARIFY": "caution", "ESCALATE": "critical"}[route]
            decision_title = {
                "ROUTINE": "Routine workflow can proceed",
                "CLARIFY": "More information is needed",
                "ESCALATE": "Qualified human review is required",
            }[route]
            escalation_value = decision.escalation_type.value if decision.escalation_type else None
            route_note = {
                "URGENT_MEDICAL": ("Urgent medical pathway directed. Qualified humans retain clinical "
                                   "authority; no external delivery verified."),
                "SCOPE_BOUNDARY": ("The requested action exceeds the delegated scope. "
                                   "Human clinical judgment is required."),
                "NON_URGENT_PROFESSIONAL": ("Recorded for review by a qualified person; "
                                            "no external delivery verified."),
            }.get(escalation_value) or {
                "ROUTINE": ("Routine routing recorded locally: follow-up may continue within the "
                            "existing specialist-approved plan. No clinical follow-up delivered."),
                "CLARIFY": ("Additional information is required before safe routing can be completed."),
            }.get(route, "")
            # Proposal known only for offline scripted replay; never claim a change otherwise.
            offline_proposal = (result.get('mode') == 'OFFLINE_SCRIPTED')
            gate_status, gate_tone, _ = gate_effect(
                decision,
                selected_case.get('expected_route') if offline_proposal else None,
                selected_case.get('expected_escalation_type') if offline_proposal else None)
            gate_chip = ((gate_tone, 'Safety Gate · ' + gate_status.split(' · ', 1)[1])
                         if decision.safety_gate_triggered
                         else ('ok', 'Safety Gate · evaluated, no override'))
            route_chips = [gate_chip]
            if escalation_value:
                route_chips.insert(0, ('neutral', 'Escalation · ' + escalation_label(decision)))

            next_kind, next_text = next_action(
                route, has_review=bool(result.get('local_review_case_id')),
                clarification_round=clarification_round, is_offline=is_offline_replay, is_live=is_nebius_live)

            route_col, next_col = st.columns([1, 1], gap='medium')
            with route_col:
                st.html(
                    '<div class="cc-route-panel tone-' + route_tone + '">'
                    '<div class="cc-panel-kicker">Final route</div>'
                    '<div class="cc-route-big">' + escape(route) + '</div>'
                    '<div class="cc-route-title">' + escape(decision_title) + '</div>'
                    '<div class="cc-route-note">' + escape(route_note) + '</div>'
                    '<div class="cc-route-meta">' + ''.join(
                        '<span class="cc-chip tone-' + tone + '">' + escape(text) + '</span>'
                        for tone, text in route_chips) + '</div></div>'
                )
            with next_col:
                with st.container(key='cc_next_action'):
                    st.html('<div class="cc-panel-kicker">Next action</div>'
                            '<div class="cc-next-text">' + escape(next_text) + '</div>')
                    if next_kind == 'open_review':
                        def _open_review_tab():
                            st.session_state['cc_v2_workspace_view'] = 'Clinical Review'

                        st.button('Next: Open simulated review', key='cc_v2_go_review',
                                  on_click=_open_review_tab, use_container_width=True)
                    st.html('<div class="cc-footnote">Internal record only · '
                            'no clinical follow-up or external message delivered.</div>')

            # URGENT_MEDICAL keeps its dedicated red dual-path treatment.
            if escalation_value == "URGENT_MEDICAL":
                st.html(
                    dedent("""
                    <div class="hero-urgent">
                        <div class="hero-title">Urgent dual-path escalation</div>
                        <div class="hero-text">
                            CareCanopy directs the case to the
                            appropriate urgent medical pathway
                            while also recording a clinical-team
                            notification action in workflow state; no external delivery verified.
                            Qualified humans retain clinical authority.
                        </div>
                    </div>
                    """)
                )
                urgent_left, urgent_right = st.columns(2)
                with urgent_left:
                    st.error("🚨 Immediate medical pathway")
                with urgent_right:
                    st.info("👥 Clinical team notification intent recorded")

            if decision.safety_gate_reason:
                st.warning("🛡️ " + decision.safety_gate_reason)

            why_html = ('<div class="cc-why"><div class="cc-panel-kicker">Why this route</div>'
                        '<div class="cc-why-text">' + escape(decision.rationale) + '</div>')
            if decision.evidence:
                why_html += ('<div class="cc-why-label">Evidence from report</div><ul>' +
                             ''.join('<li>' + escape(str(item)) + '</li>' for item in decision.evidence) +
                             '</ul>')
            st.html(why_html + '</div>')

    if show_result:
        # -----------------------------------------------------
        # CLARIFICATION WORKFLOW
        # -----------------------------------------------------

        if (
            route == "CLARIFY"
            and
            clarification_round == 0
        ):

            st.markdown(
                "### Clarification required"
            )

            with st.container(
                border=True,
            ):

                for question in (
                    decision
                    .missing_information
                ):

                    st.markdown(
                        f"- {question}"
                    )


            clarification_text = (
                st.text_area(
                    "Additional observation from the frontline worker",
                    placeholder=(
                        "Enter only the additional information "
                        "obtained from the follow-up."
                    ),
                    height=120,
                    key=(
                        "clarification_input_"
                        + str(
                            state[
                                "case_id"
                            ]
                        )
                    ),
                )
            )


            reroute_button = (
                st.button(
                    "Re-route with clarification",
                    type="primary",
                    use_container_width=True,
                )
            )


            if reroute_button:

                if is_offline_replay:
                    st.warning("Offline scripted replay cannot interpret clarification text. "
                               "Switch to a real model to test re-routing.")
                elif not (
                    clarification_text
                    .strip()
                ):

                    st.warning(
                        "Please enter the clarification "
                        "before re-routing."
                    )

                else:

                    workflow_case_id = (
                        st.session_state.get(
                            "workflow_case_id"
                        )
                        or
                        state["case_id"]
                    )

                    base_report = (
                        st.session_state.get(
                            "base_report",
                            current_report,
                        )
                    )

                    reroute_case = case_with_demo_profile(
                        selected_case, selected_profile,
                        prior_observations=patient_store.observations(patient_id, limit=5),
                    )

                    reroute_case["id"] = (
                        workflow_case_id
                    )
                    reroute_case["source_case_id"] = selected_case["id"]
                    reroute_case["synthetic_patient_id"] = "SYN-" + selected_case["id"]


                    reroute_case[
                        "current_report"
                    ] = (
                        base_report
                        + "\n\n"
                        + "Additional clarification "
                        + "provided by the frontline worker: "
                        + clarification_text.strip()
                    )


                    if is_nebius_live:
                        st.warning('Live clarification rerouting is not enabled in this '
                                   'cost-limited local pilot. No second paid call was made.')
                        st.stop()
                    with st.spinner(
                        "CareCanopy is re-routing "
                        "with the new information..."
                    ):

                        reroute_result = (
                            run_pipeline(reroute_case, is_offline_replay, nebius_live=is_nebius_live)
                        )


                    st.session_state['cc_v2_current_attempt'] = {
                        'scenario_id': selected_case['id'],
                        'execution_mode': execution_mode,
                        'outcome': 'routed',
                        'route': reroute_result['decision'].route.value,
                        'mode': reroute_result['mode'],
                    }
                    st.session_state[
                        "carecanopy_result"
                    ] = reroute_result

                    st.session_state[
                        "clarification_round"
                    ] = 1

                    st.session_state[
                        "last_clarification"
                    ] = (
                        clarification_text
                        .strip()
                    )

                    st.rerun()


        # -----------------------------------------------------
        # DETAILS & LOCAL RECORDS: one collapsed disclosure with tabs (not
        # expanders) inside, so nothing is nested. Read-only rendering:
        # opening it or switching tabs never runs a workflow action.
        # -----------------------------------------------------

        packet = action_result.get("review_packet")
        clarification_used = (st.session_state.get("last_clarification")
                              if clarification_round == 1 else None)
        detail_tab_names = ['Actions']
        if packet:
            detail_tab_names.append('Review packet')
        detail_tab_names += ['Workflow state', 'Audit trail']
        if result.get('mode') == 'NEBIUS_NEMOTRON_LIVE':
            detail_tab_names.append('Inference evidence')
        if clarification_used:
            detail_tab_names.append('Clarification used')

        with st.expander('Details & local records · internal records only', expanded=False):
            st.caption('Secondary records for this attempt. Local workflow state only; '
                       'no external delivery or clinical care completion is verified.')
            detail_tabs = dict(zip(detail_tab_names, st.tabs(detail_tab_names)))

            with detail_tabs['Actions']:
                st.caption("Permitted workflow actions recorded locally after "
                           "deterministic boundary checks; nothing was sent or delivered.")
                action_rows = []
                for action in action_result["actions"]:
                    action_name = action["action"]
                    if action_name == "REQUEST_CLARIFICATION":
                        action_note = "Clarification request created for the frontline follow-up."
                    elif action_name == "NOTIFY_CLINICAL_TEAM":
                        action_note = ("Clinical-team notification INTENT recorded "
                                       "in workflow state; no external delivery verified.")
                    else:
                        action_note = action_display_note(action_name, action.get("description", ""))
                    action_rows.append(
                        '<div class="cc-row"><div class="cc-row-title">' +
                        escape(ACTION_LABELS.get(action_name, action_name)) +
                        '</div><div class="cc-row-note">' + escape(action_note or '') + '</div></div>')
                st.html('<div class="cc-rows">' + ''.join(action_rows) + '</div>')

            if packet:
                with detail_tabs['Review packet']:
                    packet_escalation = packet.get("escalation_type")
                    triggers = packet.get("protocol_triggers", [])
                    st.html(
                        '<div class="cc-kv">' + ''.join(
                            '<div><div class="cc-kv-label">' + escape(label) +
                            '</div><div class="cc-kv-value">' + escape(value) + '</div></div>'
                            for label, value in (
                                ('Escalation', ESCALATION_LABELS.get(packet_escalation,
                                                                     packet_escalation or "—")),
                                ('Protocol trigger', ", ".join(triggers) if triggers else "—"),
                                ('Safety gate', "Applied" if packet.get("safety_gate_triggered")
                                 else "Not applied"),
                            )) + '</div>'
                    )
                    packet_html = ('<div class="cc-why"><div class="cc-why-label">Reason for human review</div>'
                                   '<div class="cc-why-text">' + escape(str(packet.get("rationale", "—"))) +
                                   '</div>')
                    packet_evidence = packet.get("evidence", [])
                    if packet_evidence:
                        packet_html += ('<div class="cc-why-label">Evidence sent to reviewer</div><ul>' +
                                        ''.join('<li>' + escape(str(item)) + '</li>'
                                                for item in packet_evidence) + '</ul>')
                    st.html(packet_html + '</div>')
                    if packet.get("safety_gate_triggered"):
                        st.warning("🛡️ " + packet.get("safety_gate_reason",
                                                     "Deterministic safety boundary applied."))
                    st.caption('Technical details (raw local packet)')
                    st.json(packet, expanded=False)

            with detail_tabs['Workflow state']:
                st.html(
                    '<div class="cc-kv">' + ''.join(
                        '<div><div class="cc-kv-label">' + escape(label) +
                        '</div><div class="cc-kv-value">' + escape(value) + '</div></div>'
                        for label, value in (
                            ('Status', workflow_status_label(state["status"], STATUS_LABELS)),
                            ('Human review', "Required" if state["review_required"] else "Not required"),
                            ('Clinical notification intent',
                             "Not sent · intent recorded" if state["clinical_team_notified"]
                             else "Not required"),
                        )) + '</div>'
                )

            with detail_tabs['Audit trail']:
                if state["audit_log"]:
                    st.dataframe(
                        [{"Action": ACTION_LABELS.get(item["action"], item["action"]),
                          "Source": item["source"],
                          "Timestamp": item["timestamp"]}
                         for item in state["audit_log"]],
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption('No local audit events recorded for this attempt.')

            if 'Inference evidence' in detail_tabs:
                with detail_tabs['Inference evidence']:
                    st.caption('Sanitized model provenance (synthetic demo). Review/handoff is '
                               'locally recorded only; no message or clinical action was delivered.')
                    st.json(result.get('model_provenance', {}), expanded=False)

            if clarification_used:
                with detail_tabs['Clarification used']:
                    st.caption('Clarification used for re-routing')
                    st.write(clarification_used)



# =========================================================
# CLINICAL REVIEW AND OUTCOMES (shared local persistent store)
# =========================================================

if active_view in ('Clinical Review', 'Outcomes & Audit'):
    _review_reason = (review_history_reason(selected_profile, service.get(chosen_review_id),
                                            service.review_context_state(chosen_review_id))
                      if chosen_review_id and service.get(chosen_review_id) else None)
    render_review_center(
        service, selected_id=chosen_review_id,
        view='review' if active_view == 'Clinical Review' else 'outcome',
        saved_record=(chosen_review_id != st.session_state.get('workflow_case_id')),
        historical_context=bool(_review_reason),
        historical_reason=_review_reason,
        # Current-attempt text comes from this session only; it never borrows a saved record.
        attempt_title=run_title,
        profile=selected_profile,
        prior_reports=patient_store.observations(patient_id, limit=5),
        live_session=is_nebius_live,
        public_demo=public_demo,
    )

# =========================================================
# INTEGRATED HUMAN REVIEW (local prototype, independent of session state)
# =========================================================


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    PUBLIC_FOOTER_NOTE if public_demo else
    'Synthetic records use local SQLite. Optional AWS v1 can use AWS services. '
    + ('Nebius live adapter is enabled for opt-in testing; check live call evidence.'
       if nebius_available else 'Nebius Nemotron runtime is not active.')
    + ' No external medical delivery is configured. Clinical decisions remain with qualified people.'
)