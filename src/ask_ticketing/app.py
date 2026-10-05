"""Ask Ticketing: minimal Streamlit interface over the existing workflow.

Run:  uv run streamlit run src/ask_ticketing/app.py

The UI only collects a question, calls `workflow.ask` on explicit submission,
and renders the stored result. Prompts, SQL safety and business logic live in
the workflow; provider construction lives in `providers.py`.
"""

from __future__ import annotations

import os

import streamlit as st

from ask_ticketing.data import DEFAULT_DB_PATH
from ask_ticketing.model import ModelError
from ask_ticketing.prompts import display_columns, display_rows
from ask_ticketing.providers import build_model
from ask_ticketing.workflow import AskResult, _error_kind, ask

# Example questions copied from the dev evaluation cases (never held-out cases).
EXAMPLES = [
    "How many tickets were sold for Brooklyn Nets home games with an event date in September 2026? Exclude refunded tickets.",
    "What were the top five events at Barclays Center by ticket revenue, for events dated January 1 through September 30, 2026?",
    "How many tickets did we sell last month?",
]

MODEL_UNAVAILABLE = "The language model is currently unavailable, so questions can't be answered right now. Please try again later."
ERROR_MESSAGES = {
    "access": MODEL_UNAVAILABLE,
    "unavailable": MODEL_UNAVAILABLE,
    "provider_request": MODEL_UNAVAILABLE,
    "auth": "The model provider's credentials are missing or invalid. See the README section \"Choose your provider\".",
    "config": "The model provider isn't configured on this machine. See the README section \"Choose your provider\".",
    "database_missing": "The ticketing data hasn't been generated yet. Run `uv run ask-ticketing-generate-data`, then try again.",
    "malformed_output": "The model couldn't produce a usable answer for this question. Try rephrasing it more specifically.",
    "invalid_result": "The generated query didn't return the event count needed to answer reliably, even after one correction. Try rephrasing the question.",
    "invalid_sql": "The generated query couldn't be run, even after one correction. Try rephrasing the question.",
    "unsafe_sql": "The generated query was blocked because it tried something other than reading the ticketing data.",
    "question_too_long": "That question is too long. Please keep it under 1,000 characters.",
    "budget": "The spending limit for model calls has been reached.",
    "limit": "The query took too long and was stopped. Try narrowing the question (for example, a shorter date range).",
}

SETUP_ERRORS = ("auth", "config", "access")  # fixable by whoever configures the provider, so show the specific fix

# Test seams: automated UI tests may put a model or database path in session state
# before the first run. The app itself never sets these.
MODEL_OVERRIDE_KEY = "_model_override"
DB_OVERRIDE_KEY = "_db_override"
PROVIDER_KEY = "_provider"  # (model, setup error), built once per session


def _md(text: str) -> str:
    """Escape '$' so Streamlit markdown doesn't render dollar amounts as LaTeX math."""
    return (text or "").replace("$", "\\$")


def _use_example(text: str) -> None:
    st.session_state["question"] = text  # fills the input only; does not ask


def _provider() -> tuple[object | None, ModelError | None]:
    """Build the configured provider once per session. Building checks local configuration
    and credentials only; it never sends a model request."""
    override = st.session_state.get(MODEL_OVERRIDE_KEY)
    if override is not None:
        return override, None
    if PROVIDER_KEY not in st.session_state:
        try:
            st.session_state[PROVIDER_KEY] = (build_model(), None)
        except ModelError as exc:
            st.session_state[PROVIDER_KEY] = (None, exc)
    return st.session_state[PROVIDER_KEY]


def _run_question(question: str) -> AskResult:
    db_path = st.session_state.get(DB_OVERRIDE_KEY) or os.environ.get("ASK_TICKETING_DB", str(DEFAULT_DB_PATH))
    model, setup_error = _provider()
    if setup_error is not None:
        return AskResult(question, "error", error_kind=_error_kind(setup_error), error=str(setup_error))
    return ask(question, model, db_path)


def _render(result: AskResult) -> None:
    if result.status == "answered":
        st.markdown(f"**{_md(result.answer)}**")
    elif result.status == "clarify":
        st.info(f"**I need one detail first:** {_md(result.answer)}\n\nEdit your question above and ask again.")
    elif result.status == "unsupported":
        st.warning(f"**This data can't answer that.** {_md(result.answer)}")
    elif result.error_kind in SETUP_ERRORS and result.error:
        # Adapter messages are user-safe by contract (no credentials or raw payloads) and
        # name the provider-specific fix, e.g. which environment variable is missing.
        st.error(f"**Model provider setup needed.** {_md(result.error)}")
    else:
        st.error(ERROR_MESSAGES.get(result.error_kind, "Something went wrong while answering. Please try again."))

    if result.assumptions:
        st.markdown("**Assumptions**")
        st.markdown("\n".join(f"- {_md(a)}" for a in result.assumptions))

    if result.status == "answered":
        if result.rows:
            if result.truncated:
                st.warning(f"Showing only the first {len(result.rows)} rows; the full result is longer.")
            rows = display_rows(result.columns, result.rows)
            table = {name: [row[i] for row in rows] for i, name in enumerate(display_columns(result.columns))}
            st.dataframe(table, hide_index=True, use_container_width=True)
        else:
            st.info("No rows matched this question. This means nothing matched, not a total of zero.")

    if result.sql:
        with st.expander("SQL executed" if result.status == "answered" else "SQL (not run successfully)"):
            st.code(result.sql, language="sql", wrap_lines=True)


def main() -> None:
    st.set_page_config(page_title="Ask Ticketing", layout="centered")
    st.title("Ask Ticketing")
    st.markdown("Get the numbers for your next client meeting.")
    st.caption("Synthetic ticketing data · As of October 1, 2026")

    _, setup_error = _provider()
    if setup_error is not None:
        st.error(f"**Model provider setup needed.** {_md(str(setup_error))}")
        st.caption("Asking is disabled until this is fixed. Set the variable in the shell that runs the app, then restart the app (Ctrl+C, then run it again).")

    st.markdown("**Try an example**")
    for i, example in enumerate(EXAMPLES):
        st.button(example, key=f"example_{i}", on_click=_use_example, args=(example,), use_container_width=True)

    with st.form("ask_form"):
        st.text_area("Your question", key="question", height=90, placeholder="e.g. How many tickets were sold for Liberty home games in August 2026?")
        submitted = st.form_submit_button("Ask", type="primary", disabled=setup_error is not None)

    # Only an explicit submission calls the workflow; other reruns re-render the stored result.
    if submitted:
        question = (st.session_state.get("question") or "").strip()
        if not question:
            st.session_state.pop("result", None)
            st.warning("Type a question first.")
        else:
            with st.spinner("Finding the numbers…"):
                st.session_state["result"] = _run_question(question)

    result = st.session_state.get("result")
    if result is not None:
        st.divider()
        st.caption(f"Question: {_md(result.question)}")
        _render(result)


main()
