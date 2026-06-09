from app import database
from app.models import Account


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"code": 1, "msg": "ok"}


def test_api_requires_key(client):
    resp = client.get("/api/accounts")
    assert resp.status_code == 401
    assert resp.json() == {"code": 401, "msg": "unauthorized"}


def test_api_rejects_wrong_key(client):
    resp = client.get("/api/accounts", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_import_and_list_accounts(client, api_headers):
    text = ("a@hotmail.com----pass----cid----rt-a\n"
            "b@hotmail.com----pass----cid----rt-b----extra----parts")
    resp = client.post("/api/accounts/import", headers=api_headers, json={"text": text})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 1
    assert body["data"]["imported"] == 2

    resp = client.get("/api/accounts", headers=api_headers)
    data = resp.json()["data"]
    assert {d["email"] for d in data} == {"a@hotmail.com", "b@hotmail.com"}
    for d in data:
        assert d["has_refresh_token"] is True
        assert "password" not in d
        assert "refresh_token" not in d
        assert "access_token" not in d


def test_code_returns_cached_match(client, api_headers):
    database.upsert_account(Account("c@hotmail.com", "p", "cid", "rt"))
    database.insert_message("c@hotmail.com", "mid-1", "noreply@svc.com",
                            "Your code is 654321", "body text", "", "date", "654321")
    resp = client.post("/api/code", headers=api_headers,
                       json={"email": "c@hotmail.com", "timeout": 5,
                             "pattern": r"\b\d{6}\b"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 1
    assert body["data"]["verification_code"] == "654321"


def test_code_since_uses_message_date_not_first_seen_time(client, api_headers):
    database.upsert_account(Account("fresh@hotmail.com", "p", "cid", "rt"))
    # This stale code is inserted now, but the email Date is before the send
    # timestamp. It must not be returned for the new verification attempt.
    database.insert_message(
        "fresh@hotmail.com", "old", "noreply@tm.openai.com",
        "ChatGPT code 111111", "", "",
        "Tue, 09 Jun 2026 12:00:00 +0000", "111111",
    )
    database.insert_message(
        "fresh@hotmail.com", "new", "noreply@tm.openai.com",
        "ChatGPT code 222222", "", "",
        "Tue, 09 Jun 2026 12:10:00 +0000", "222222",
    )

    since = 1781006700  # Tue, 09 Jun 2026 12:05:00 +0000
    resp = client.post("/api/code", headers=api_headers,
                       json={"email": "fresh@hotmail.com", "timeout": 0,
                             "pattern": r"\b\d{6}\b", "subject_keyword": "ChatGPT",
                             "from_keyword": "openai", "since": since})
    assert resp.status_code == 200
    assert resp.json()["data"]["verification_code"] == "222222"


def test_code_since_rejects_stale_message_even_if_recently_inserted(client, api_headers):
    database.upsert_account(Account("stale@hotmail.com", "p", "cid", "rt"))
    database.insert_message(
        "stale@hotmail.com", "old", "noreply@tm.openai.com",
        "ChatGPT code 111111", "", "",
        "Tue, 09 Jun 2026 12:00:00 +0000", "111111",
    )

    since = 1781006700  # Tue, 09 Jun 2026 12:05:00 +0000
    resp = client.post("/api/code", headers=api_headers,
                       json={"email": "stale@hotmail.com", "timeout": 0,
                             "pattern": r"\b\d{6}\b", "subject_keyword": "ChatGPT",
                             "from_keyword": "openai", "since": since})
    assert resp.status_code == 408


def test_code_since_grace_does_not_reallow_stale_message_with_date(client, api_headers):
    database.upsert_account(Account("grace@hotmail.com", "p", "cid", "rt"))
    database.insert_message(
        "grace@hotmail.com", "old", "noreply@tm.openai.com",
        "ChatGPT code 111111", "", "",
        "Tue, 09 Jun 2026 12:04:30 +0000", "111111",
    )

    since = 1781006700  # Tue, 09 Jun 2026 12:05:00 +0000
    resp = client.post("/api/code", headers=api_headers,
                       json={"email": "grace@hotmail.com", "timeout": 0,
                             "pattern": r"\b\d{6}\b", "subject_keyword": "ChatGPT",
                             "from_keyword": "openai", "since": since})
    assert resp.status_code == 408


def test_code_timeout(client, api_headers):
    database.upsert_account(Account("d@hotmail.com", "p", "cid", "rt"))
    # timeout=0 -> no polling occurs, immediate 408 (no network access needed).
    resp = client.post("/api/code", headers=api_headers,
                       json={"email": "d@hotmail.com", "timeout": 0,
                             "pattern": r"\b\d{6}\b"})
    assert resp.status_code == 408
    body = resp.json()
    assert body["code"] == 408
    assert body["data"]["email"] == "d@hotmail.com"


def test_code_account_not_found(client, api_headers):
    resp = client.post("/api/code", headers=api_headers,
                       json={"email": "missing@hotmail.com", "timeout": 0})
    assert resp.status_code == 404
    assert resp.json()["code"] == 404


def test_messages_excludes_full_body(client, api_headers):
    database.upsert_account(Account("m@hotmail.com", "p", "cid", "rt"))
    database.insert_message("m@hotmail.com", "mid-9", "noreply@svc.com",
                            "Subject here", "SECRET BODY", "<b>SECRET</b>",
                            "date", "123456")
    resp = client.get("/api/messages", headers=api_headers,
                      params={"email_addr": "m@hotmail.com"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    item = data[0]
    assert item["code"] == "123456"
    assert item["subject"] == "Subject here"
    assert "body" not in item
    assert "html_body" not in item


def test_subject_keyword_filters_out_non_matching(client, api_headers):
    database.upsert_account(Account("f@hotmail.com", "p", "cid", "rt"))
    database.insert_message("f@hotmail.com", "mid-1", "noreply@svc.com",
                            "newsletter 111111", "", "", "date", "111111")
    # subject_keyword that does not match -> nothing found -> 408 with timeout=0
    resp = client.post("/api/code", headers=api_headers,
                       json={"email": "f@hotmail.com", "timeout": 0,
                             "pattern": r"\b\d{6}\b", "subject_keyword": "verification"})
    assert resp.status_code == 408
