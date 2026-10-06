"""Ask Ticketing: minimal Streamlit interface over the existing workflow.

Run:  uv run streamlit run src/ask_ticketing/app.py

The UI only collects a question, calls `workflow.ask` when Ask is clicked, and
renders the stored result. Prompts, SQL safety and business logic live in the
workflow; provider construction lives in `providers.py`. Saved questions (text
only, local JSON file) live in `saved_questions.py`; saving, selecting and
removing them never call the model.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import streamlit as st

from ask_ticketing.data import DEFAULT_DB_PATH
from ask_ticketing.model import ModelError
from ask_ticketing.prompts import display_rows
from ask_ticketing.providers import build_model
from ask_ticketing import saved_questions as sq
from ask_ticketing.export import export_result
from ask_ticketing.presentation import aggregate_note, column_labels, markdown_literal
from ask_ticketing.workflow import AskResult, _error_kind, ask

# Compact labelled examples: (label, full question). Clicking fills the question box only.
# None of these are held-out evaluation questions; the evaluation set itself is unchanged.
EXAMPLES = [
    ("Nets ticket sales",
     "How many tickets were sold for Brooklyn Nets home games with an event date in September 2026?"),
    ("Upcoming event overview",
     "For each event after October 1, 2026, show its name, event date, venue, sold ticket count, remaining capacity, "
     "sell-through percentage, and average sold-ticket price. Exclude refunded tickets from sales and price calculations. "
     "Include events with no sales, showing their average sold-ticket price as unavailable. Order by event date, then event ID."),
    ("Revenue by venue",
     "For events dated August 1 through August 31, 2026, show ticket revenue by venue, excluding refunded tickets. "
     "Order by revenue descending, then venue name."),
]

WHAT_CAN_I_ASK = """This synthetic dataset covers:
- events, dates, venues and categories
- ticket prices and revenue
- sold and refunded ticket counts
- capacity, remaining inventory and sell-through
- sales by purchase date or event date

It has no ticket types, VIP tiers, seat sections, customers or sales channels."""

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
SAVED_PATH_OVERRIDE_KEY = "_saved_path_override"
SAVE_FEEDBACK_KEY = "_save_feedback"  # ("saved" | "duplicate" | "error", question text or message); shown once
PROVIDER_KEY = "_provider"  # (model, setup error), built once per session


def _md(text: str) -> str:
    """Escape '$' so Streamlit markdown doesn't render dollar amounts as LaTeX math."""
    return (text or "").replace("$", "\\$")


def _use_example(text: str) -> None:
    st.session_state["question"] = text  # fills the input only; does not ask
    st.session_state.pop(SAVE_FEEDBACK_KEY, None)  # the save confirmation is for a save action, not a selection


def _saved_path() -> Path:
    return Path(st.session_state.get(SAVED_PATH_OVERRIDE_KEY) or sq.default_path())


def _save_current() -> None:
    """Save-button callback: stores the current input's text. Never calls the model."""
    text = sq.normalize(st.session_state.get("question", ""))
    try:
        _, added = sq.add(_saved_path(), text)
        st.session_state[SAVE_FEEDBACK_KEY] = ("saved" if added else "duplicate", text)
    except sq.SavedQuestionsError as exc:
        st.session_state[SAVE_FEEDBACK_KEY] = ("error", str(exc))


CLEAR_HELP = "Clear this question and its result. Saved questions are kept."


def _clear_workspace() -> None:
    """Clear-button callback: resets only this session's question, save confirmation and
    result (answer, assumptions, table, SQL and its download all render from the result).
    Saved questions on disk, provider setup and credentials are untouched; setup and storage
    warnings are recomputed on every run, so they stay visible. Never calls the model or SQL."""
    st.session_state["question"] = ""
    st.session_state.pop(SAVE_FEEDBACK_KEY, None)
    st.session_state.pop("result", None)


def _remove_saved(text: str) -> None:
    try:
        sq.remove(_saved_path(), text)
        st.session_state.pop(SAVE_FEEDBACK_KEY, None)
    except sq.SavedQuestionsError as exc:
        st.session_state[SAVE_FEEDBACK_KEY] = ("error", str(exc))


def _saved_key(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.sha1(text.encode()).hexdigest()[:12]}"


PREVIEW_CHARS = 60


def _preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """Display-only shortening for a collapsed saved question (word boundary + "…").
    The saved text itself is never changed; the expanded entry shows it in full."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0] or flat[:limit]
    return cut.rstrip(" ,.;:") + "…"


def _render_saved_sidebar(saved: list[str], store_error: str | None) -> None:
    """Saved questions in the sidebar as compact, collapsed entries (newest first). Each label
    is a display-only preview; expanding shows the COMPLETE original wording as literal text
    with Use question / Remove. No nested scrolling containers. Never calls the model."""
    sidebar = st.sidebar
    sidebar.subheader("Saved questions")
    if store_error:
        sidebar.warning(f"**Saved questions unavailable.** {store_error}")
    elif not saved:
        sidebar.caption("No saved questions yet. Type a question and choose Save question.")
    for text in reversed(saved):  # newest first
        entry = sidebar.expander(markdown_literal(_preview(text)), expanded=False, key=_saved_key("saved_entry", text))
        entry.markdown(markdown_literal(text))  # literal: the user's full wording, nothing rendered
        controls = entry.container(horizontal=True, wrap=True, gap="small")
        controls.button("Use question", key=_saved_key("saved_use", text), on_click=_use_example, args=(text,),
                        help="Put this question in the question box. It does not ask it.", width="content")
        controls.button("Remove", key=_saved_key("saved_remove", text), on_click=_remove_saved, args=(text,),
                        type="tertiary", help="Remove this saved question.", width="content")


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
    # Prose stays in a readable column; the supporting table gets the full width below.
    prose, _ = st.columns([3, 2])
    with prose:
        st.caption(f"Question: {_md(result.question)}")
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
            heading, action = st.columns([5, 1], vertical_alignment="bottom")
            heading.markdown("**Supporting rows**")
            with action:
                export = _render_download(result)
            if result.truncated:
                st.warning(f"Showing only the first {len(result.rows)} rows; the full result is longer. "
                           "The download is partial too. Narrow the question to see and export everything.")
            # Same rows and order as returned; only the headers are made readable and money is
            # shown in dollars. st.table is static (no toolbar), so "Download CSV" is the only
            # export. It renders Markdown, so every header and text cell is escaped first.
            rows = display_rows(result.columns, result.rows)
            labels = [markdown_literal(label) for label in column_labels(result.columns)]
            table = {label: [markdown_literal(row[i]) for row in rows] for i, label in enumerate(labels)}
            st.table(table, hide_index=True, height=420 if len(rows) > 12 else "content")
            note = aggregate_note(result.columns, result.rows)
            if note:
                st.caption(note)
            if export is not None:
                st.caption(f"Download CSV exports the {len(result.rows)} rows shown for: \"{_md(result.question)}\". "
                           "CSV uses raw column names; monetary \\_cents columns are in cents.")
        else:
            st.info("No rows matched this question. This means nothing matched, not a total of zero.")

    if result.sql:
        with st.expander("SQL executed" if result.status == "answered" else "SQL (not run successfully)"):
            st.code(result.sql, language="sql", wrap_lines=True)


def _render_download(result: AskResult):
    """CSV of the displayed result (not the current input). Built from the stored rows, so
    downloading never calls the model or reruns SQL; on_click="ignore" avoids even a rerun."""
    export = export_result(result)
    if export is not None:
        st.download_button("Download CSV", data=export.data, file_name=export.filename, mime=export.mime_type,
                           key="download_csv", on_click="ignore", width="content")
    return export


def _save_status(current: str, saved: list[str]) -> str | None:
    """Status text for the Save action, kept separate from the button (whose label never
    changes). A one-time confirmation follows a save; otherwise a quiet membership status
    describes the current text. Both are computed from the current input, so edits clear them."""
    feedback = st.session_state.pop(SAVE_FEEDBACK_KEY, None)  # shown once, then gone
    if feedback and feedback[0] == "error":
        st.warning(feedback[1])
    elif feedback and feedback[1] == current:
        return "✓ Question saved" if feedback[0] == "saved" else "Already in saved questions. Not added again."
    if sq.is_saved(saved, current):
        return "Already in saved questions"
    return None


def main() -> None:
    st.set_page_config(page_title="Ask Ticketing", layout="wide", initial_sidebar_state="auto")
    st.header("Ask Ticketing")
    st.caption("Get the numbers for your next client meeting. · Synthetic ticketing data · As of October 1, 2026")

    _, setup_error = _provider()
    if setup_error is not None:
        st.error(f"**Model provider setup needed.** {_md(str(setup_error))}")
        st.caption("Asking is disabled until this is fixed. Set the variable in the shell that runs the app, then restart the app (Ctrl+C, then run it again).")

    try:
        saved, store_error = sq.load(_saved_path()), None
    except sq.SavedQuestionsError as exc:
        saved, store_error = [], str(exc)
    _render_saved_sidebar(saved, store_error)

    # Compact labelled examples: each fills the full question; none of them asks.
    examples = st.container(horizontal=True, wrap=True, gap="small", vertical_alignment="center")
    examples.markdown("**Try an example:**", width="content")
    for i, (label, question) in enumerate(EXAMPLES):
        examples.button(label, key=f"example_{i}", on_click=_use_example, args=(question,), help=question, width="content")

    question_col, _ = st.columns([3, 2])
    with question_col:
        # The label row carries the dataset guidance as a compact popover, so it is reachable
        # without scrolling and opening it does not push the answer down.
        label_row = st.container(horizontal=True, gap="small", vertical_alignment="center")
        label_row.markdown("Your question", width="content")
        with label_row.popover("What can I ask?", type="tertiary", width="content"):
            st.markdown(WHAT_CAN_I_ASK)
        st.text_area("Your question", key="question", height=110, label_visibility="collapsed",
                     placeholder="e.g. How many tickets were sold for Liberty home games in August 2026?")
        current = sq.normalize(st.session_state.get("question", ""))
        actions = st.container(horizontal=True, wrap=True, gap="small", vertical_alignment="center")
        submitted = actions.button("Ask", key="ask", type="primary", disabled=setup_error is not None, width="content")
        # Save stays clickable even for blank or already-saved text: a button disabled at the
        # moment of a click would swallow a click made right after typing, because Streamlit
        # applies the typed text in that same click. The callback rejects blanks and duplicates.
        actions.button("Save question", key="save_question", on_click=_save_current,
                       disabled=store_error is not None, width="content",
                       help="Save this question to reuse it later. Saving never runs the question.")
        actions.button("× Clear", key="clear", on_click=_clear_workspace, help=CLEAR_HELP, width="content")
        status = _save_status(current, saved)
        if status:
            actions.caption(status, width="content")

    # Only Ask calls the workflow; every other rerun re-renders the stored result.
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
        _render(result)

main()
