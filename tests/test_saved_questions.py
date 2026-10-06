"""Saved-questions storage: plain file tests, no Streamlit and no model."""

import json
import os

import pytest

from ask_ticketing import saved_questions as sq


def test_missing_file_means_no_saved_questions(tmp_path):
    assert sq.load(tmp_path / "none.json") == []


def test_add_persists_text_only_and_survives_reload(tmp_path):
    path = tmp_path / "saved.json"
    questions, added = sq.add(path, "  How many tickets did we sell last month?  ")
    assert added and questions == ["How many tickets did we sell last month?"]
    assert json.loads(path.read_text()) == {"version": 1, "questions": ["How many tickets did we sell last month?"]}
    assert sq.load(path) == questions  # a fresh read, as after an app restart


def test_duplicates_ignore_case_and_whitespace(tmp_path):
    path = tmp_path / "saved.json"
    sq.add(path, "Top five events by revenue")
    questions, added = sq.add(path, "  top  FIVE events by revenue ")
    assert not added and questions == ["Top five events by revenue"]
    assert sq.is_saved(questions, "TOP five events  by revenue")
    assert not sq.is_saved(questions, "Top five events by tickets")
    assert not sq.is_saved(questions, "   ")


@pytest.mark.parametrize("text", ["", "   ", "x" * 1001])
def test_blank_or_overlong_text_is_rejected(tmp_path, text):
    with pytest.raises(sq.SavedQuestionsError):
        sq.add(tmp_path / "saved.json", text)
    assert not (tmp_path / "saved.json").exists()


def test_number_of_saved_questions_is_bounded(tmp_path):
    path = tmp_path / "saved.json"
    for i in range(sq.MAX_SAVED):
        sq.add(path, f"question {i}")
    with pytest.raises(sq.SavedQuestionsError):
        sq.add(path, "one more")
    assert len(sq.load(path)) == sq.MAX_SAVED


def test_remove_deletes_only_that_question(tmp_path):
    path = tmp_path / "saved.json"
    sq.add(path, "A")
    sq.add(path, "B")
    assert sq.remove(path, "A") == ["B"] and sq.load(path) == ["B"]
    assert sq.remove(path, "not there") == ["B"]


@pytest.mark.parametrize("content", ["{not json", '{"questions": "not a list"}', '["a", "b"]', '{"questions": ["ok", 3]}'])
def test_malformed_file_is_reported_and_never_overwritten(tmp_path, content):
    path = tmp_path / "saved.json"
    path.write_text(content)
    with pytest.raises(sq.SavedQuestionsError) as info:
        sq.load(path)
    assert "left unchanged" in str(info.value)
    for action in (lambda: sq.add(path, "New question"), lambda: sq.remove(path, "ok")):
        with pytest.raises(sq.SavedQuestionsError):
            action()
    assert path.read_text() == content


def test_unwritable_location_is_reported(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("a file where the directory should be")
    with pytest.raises(sq.SavedQuestionsError) as info:
        sq.add(blocker / "saved.json", "A question")
    assert "couldn't be written" in str(info.value)


def test_read_only_directory_keeps_the_previous_file(tmp_path):
    path = tmp_path / "ro" / "saved.json"
    sq.add(path, "Kept")
    os.chmod(path.parent, 0o500)
    try:
        with pytest.raises(sq.SavedQuestionsError):
            sq.add(path, "Not written")
        assert sq.load(path) == ["Kept"]
    finally:
        os.chmod(path.parent, 0o700)


def test_failed_atomic_replace_leaves_original_and_no_temp_files(tmp_path, monkeypatch):
    path = tmp_path / "saved.json"
    sq.add(path, "Original")

    def boom(*args, **kwargs):
        raise OSError("simulated failure")

    monkeypatch.setattr(sq.os, "replace", boom)
    with pytest.raises(sq.SavedQuestionsError):
        sq.add(path, "Second")
    assert sq.load(path) == ["Original"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["saved.json"]


def test_default_path_is_gitignored_and_separate_from_the_database(monkeypatch):
    monkeypatch.delenv("ASK_TICKETING_SAVED_QUESTIONS", raising=False)
    from ask_ticketing.data import DEFAULT_DB_PATH

    assert sq.default_path() == sq.DEFAULT_PATH != DEFAULT_DB_PATH
    assert sq.DEFAULT_PATH.parent == DEFAULT_DB_PATH.parent  # data/local/, which .gitignore excludes
