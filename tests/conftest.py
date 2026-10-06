import pytest

from ask_ticketing.data import build_database, connect, generate_dataset


@pytest.fixture(scope="session")
def dataset():
    return generate_dataset()


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    return build_database(tmp_path_factory.mktemp("db") / "ticketing.db")


@pytest.fixture(scope="session")
def db(db_path):
    conn = connect(db_path)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def isolated_saved_questions(tmp_path, monkeypatch):
    """Every test gets its own saved-questions file; tests never touch data/local."""
    path = tmp_path / "saved_questions.json"
    monkeypatch.setenv("ASK_TICKETING_SAVED_QUESTIONS", str(path))
    return path
