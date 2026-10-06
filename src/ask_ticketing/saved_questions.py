"""Saved questions: question text a user wants to reuse, kept in a small local JSON file.

Only question text is stored (no answers, SQL, credentials or history). The file belongs to
this local app installation and is shared by everyone who uses it; there are no users.

File format: {"version": 1, "questions": ["...", ...]}, newest last.
- A missing file means no saved questions.
- A malformed or unreadable file raises SavedQuestionsError and is never overwritten, so a
  person can inspect or fix it.
- Writes are atomic: a temporary file in the same directory, then os.replace.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ask_ticketing.workflow import MAX_QUESTION_CHARS

DEFAULT_PATH = Path("data/local/saved_questions.json")  # gitignored, separate from the database
MAX_SAVED = 50
VERSION = 1


class SavedQuestionsError(Exception):
    """The saved-questions file can't be read or written. The message is safe to show."""


def default_path() -> Path:
    return Path(os.environ.get("ASK_TICKETING_SAVED_QUESTIONS") or DEFAULT_PATH)


def normalize(text: str) -> str:
    """The form a question is stored in: surrounding whitespace removed."""
    return (text or "").strip()


def _same(a: str, b: str) -> bool:
    """Duplicates ignore case and repeated whitespace."""
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def load(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise SavedQuestionsError(f"Saved questions couldn't be read from {path} ({type(exc).__name__}).") from None
    except json.JSONDecodeError:
        raise SavedQuestionsError(f"The saved-questions file {path} is not valid JSON. It was left unchanged; fix or move it to start fresh.") from None
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise SavedQuestionsError(f"The saved-questions file {path} has an unexpected format. It was left unchanged; fix or move it to start fresh.")
    return questions


def _write(path: Path, questions: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".saved_questions.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": VERSION, "questions": questions}, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise SavedQuestionsError(f"Saved questions couldn't be written to {path} ({type(exc).__name__}).") from None


def is_saved(questions: list[str], text: str) -> bool:
    text = normalize(text)
    return bool(text) and any(_same(q, text) for q in questions)


def add(path: Path, text: str) -> tuple[list[str], bool]:
    """Save `text`. Returns (questions, added). A duplicate is not added again."""
    text = normalize(text)
    if not text:
        raise SavedQuestionsError("Type a question before saving it.")
    if len(text) > MAX_QUESTION_CHARS:
        raise SavedQuestionsError(f"Questions longer than {MAX_QUESTION_CHARS:,} characters can't be saved.")
    questions = load(path)  # raises on a malformed file, so it is never overwritten
    if is_saved(questions, text):
        return questions, False
    if len(questions) >= MAX_SAVED:
        raise SavedQuestionsError(f"You can save up to {MAX_SAVED} questions. Remove one first.")
    questions = [*questions, text]
    _write(path, questions)
    return questions, True


def remove(path: Path, text: str) -> list[str]:
    questions = load(path)
    kept = [q for q in questions if q != text]
    if kept != questions:
        _write(path, kept)
    return kept
