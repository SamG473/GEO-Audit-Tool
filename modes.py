"""Demo mode and the live-audit password (no Streamlit, no API calls).

The app opens in demo mode: an invented business walked through the normal flow
on canned data from the committed `demo_run.json`, with zero API calls. Live
audits, which cost real money, are only offered when AUDIT_PASSWORD is set in
the environment, and only after a visitor has entered it.
"""
import hmac
import json
import os

DEMO_LABEL = "Demo: sample data, no live searches"
DEMO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_run.json")


def load_demo_run():
    """The canned demo audit, in the same shape as a saved live run."""
    with open(DEMO_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def reset_wizard(state):
    """Clear everything one run leaves behind in `state` (st.session_state), so the
    next run starts clean on the first screen. The demo/live mode is kept."""
    state["step"] = 1
    state["answers"] = {}
    for key in ("qs", "job", "run_notice", "bad_password", "open_editing", "editing",
                "in_business", "in_town", "in_password"):
        state.pop(key, None)


def audit_password():
    """The password that unlocks live audits, or None if live audits are off."""
    return os.environ.get("AUDIT_PASSWORD") or None


def password_ok(attempt):
    """True only if live audits are enabled and `attempt` matches the password."""
    expected = audit_password()
    if not expected or not attempt:
        return False
    return hmac.compare_digest(attempt.encode("utf-8"), expected.encode("utf-8"))
