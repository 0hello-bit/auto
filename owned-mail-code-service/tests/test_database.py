import pytest

from app import database
from app.models import Account


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "t.db"
    monkeypatch.setattr(database.settings, "DB_FILE", str(db_file))
    database.init_db()
    return str(db_file)


def test_insert_message_dedup(tmp_db):
    args = ("u@x.com", "mid-1", "f@x.com", "subj", "body", "<html>", "date", "123456")
    assert database.insert_message(*args) is True
    # Same (email, message_id) -> ignored.
    assert database.insert_message(*args) is False
    rows = database.get_recent_messages("u@x.com", 30)
    assert len(rows) == 1


def test_account_roundtrip_and_public_view_hides_secrets(tmp_db):
    database.upsert_account(Account("u@x.com", "secret-pass", "cid", "secret-rt"))
    public = database.list_accounts_public()
    assert len(public) == 1
    row = public[0]
    assert row["email"] == "u@x.com"
    assert row["client_id"] == "cid"
    assert row["has_refresh_token"] is True
    assert "password" not in row
    assert "refresh_token" not in row
    assert "access_token" not in row


def test_upsert_account_updates_existing(tmp_db):
    database.upsert_account(Account("u@x.com", "p1", "c1", "t1"))
    database.upsert_account(Account("u@x.com", "p2", "c2", "t2"))
    acc = database.get_account("u@x.com")
    assert acc.refresh_token == "t2"
    assert len(database.get_accounts()) == 1


def test_poll_status_upsert(tmp_db):
    database.update_poll_status("u@x.com", 1000, "")
    database.update_poll_status("u@x.com", 2000, "boom")
    status = database.get_poll_status("u@x.com")
    assert status["last_poll_at"] == 2000
    assert status["last_error"] == "boom"
