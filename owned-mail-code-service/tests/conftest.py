"""Pytest fixtures.

Environment variables are set here, BEFORE the application modules are imported,
so that `app.config.settings` picks up the test configuration.
"""
import os
import tempfile

import pytest

# Configure environment before any `app` import happens.
os.environ.setdefault("API_KEY", "test-secret-key")
os.environ["ACCOUNTS_FILE"] = ""  # do not auto-load an accounts file
os.environ["DB_FILE"] = os.path.join(tempfile.gettempdir(), "test_mail_codes.db")
os.environ["POLL_INTERVAL_SECONDS"] = "3600"

API_KEY = os.environ["API_KEY"]


def _reset_db():
    from app import database
    for path in (database.settings.DB_FILE,
                 database.settings.DB_FILE + "-wal",
                 database.settings.DB_FILE + "-shm"):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    database.init_db()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app import main

    _reset_db()
    # Instantiate WITHOUT the context manager so the lifespan (and the
    # background poller) does not start during tests.
    return TestClient(main.app)


@pytest.fixture()
def api_headers():
    return {"x-api-key": API_KEY}
