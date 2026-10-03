import pytest
from datetime import datetime, timezone
from unittest.mock import patch

with patch("boto3.client") as boto_client:
    boto_client.return_value.get_parameters.return_value = {"Parameters": []}
    import main

from main import app, check_in_view_data


class FakeTable:
    def __init__(self, item=None):
        self.item = item
        self.updates = []

    def get_item(self, Key):
        return {"Item": self.item} if self.item else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        return {}

    def scan(self, **kwargs):
        return {"Items": [self.item]} if self.item else {"Items": []}


@pytest.fixture
def client():
    """Configures Flask app in testing mode."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def sign_in(client, sub="cognito-test-user"):
    with client.session_transaction() as current_session:
        current_session["user"] = {"sub": sub, "email": "person@example.com"}


# ==========================================
# 1. UNIT TESTS (Testing Business Logic)
# ==========================================

def test_check_in_view_data_no_checkin():
    """Verify check-in view formatting when user has never checked in."""
    data = check_in_view_data(last_check_in=None)
    
    assert data["last_check_in_label"] == "Not yet"
    assert "check_in_deadline" in data


def test_check_in_view_data_with_timestamp():
    """Verify check-in view formatting with a valid timestamp."""
    now = datetime.now(timezone.utc)
    data = check_in_view_data(last_check_in=now)
    
    assert data["last_check_in_label"] == "Not yet"
    assert "check_in_deadline" in data


def test_check_in_view_data_with_iso_timestamp():
    data = check_in_view_data("2026-09-25T12:00:00+00:00")

    assert data["last_check_in_label"] == "Not yet"
    assert "check_in_deadline" in data


# ==========================================
# 2. INTEGRATION TESTS (Testing Endpoints)
# ==========================================

def test_cron_check_unauthorized(client, monkeypatch):
    """Verify /api/cron-check rejects requests without the configured token."""
    monkeypatch.setattr(main, "CRON_AUTH_TOKEN", "expected-token")
    response = client.post("/api/cron-check")
    
    assert response.status_code == 401
    assert b"Unauthorized invocation block." in response.data


def test_cron_check_rejects_invalid_bearer_token(client, monkeypatch):
    monkeypatch.setattr(main, "CRON_AUTH_TOKEN", "expected-token")

    response = client.post(
        "/api/cron-check",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401


def test_cron_check_accepts_valid_bearer_token(client, monkeypatch):
    monkeypatch.setattr(main, "CRON_AUTH_TOKEN", "expected-token")
    monkeypatch.setattr(
        main,
        "table",
        FakeTable({
            "id": "cognito-test-user",
            "last_check_in": datetime.now(timezone.utc).isoformat(),
        }),
    )

    response = client.post(
        "/api/cron-check",
        headers={"Authorization": "Bearer expected-token"},
    )

    assert response.status_code == 200
    assert response.json["status"] == "Heartbeat calculated successfully."


def test_dashboard_reads_dynamodb_item(client, monkeypatch):
    monkeypatch.setattr(main, "table", FakeTable({"id": "cognito-test-user"}))
    sign_in(client)

    response = client.get("/")

    assert response.status_code == 200
    assert b"Not yet" in response.data


def test_dashboard_shows_sign_in_page_when_logged_out(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Sign in with Amazon Cognito" in response.data


def test_cognito_callback_stores_user_identity(client, monkeypatch):
    monkeypatch.setattr(
        main.oauth.oidc,
        "authorize_access_token",
        lambda: {"userinfo": {"sub": "cognito-test-user", "email": "person@example.com"}},
    )

    response = client.get("/auth/callback")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/contacts")
    with client.session_transaction() as current_session:
        assert current_session["user"] == {
            "sub": "cognito-test-user",
            "email": "person@example.com",
        }


def test_contacts_page_redirects_to_login_when_logged_out(client):
    response = client.get("/contacts")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_contacts_page_shows_saved_emails_when_they_exist(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "table",
        FakeTable({
            "id": "cognito-test-user",
            "contacts": {
                "my_email": "me@example.com",
                "family_email": "family@example.com",
            },
        }),
    )
    sign_in(client)

    response = client.get("/contacts")

    assert response.status_code == 200
    assert b"me@example.com" in response.data
    assert b"family@example.com" in response.data


def test_check_in_requires_sign_in(client):
    response = client.post("/api/check-in")

    assert response.status_code == 401


def test_contacts_page_saves_both_email_addresses(client, monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(main, "table", fake_table)
    sign_in(client)

    response = client.post(
        "/contacts",
        data={"my_email": "me@example.com", "family_email": "family@example.com"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert fake_table.updates[0]["ExpressionAttributeValues"][":contacts"] == {
        "my_email": "me@example.com",
        "family_email": "family@example.com",
    }


def test_contacts_page_rejects_invalid_email(client, monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(main, "table", fake_table)
    sign_in(client)

    response = client.post(
        "/contacts",
        data={"my_email": "not-an-email", "family_email": "family@example.com"},
    )

    assert response.status_code == 400
    assert b"Enter a valid email address in both fields." in response.data
    assert fake_table.updates == []


def test_check_in_updates_dynamodb_item(client, monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(main, "table", fake_table)
    sign_in(client)

    response = client.post("/api/check-in")

    assert response.status_code == 200
    assert fake_table.updates[0]["Key"] == {"id": "cognito-test-user"}
    assert fake_table.updates[0]["ExpressionAttributeValues"][":status"] == "OK"
    assert ":contacts" not in fake_table.updates[0]["ExpressionAttributeValues"]
    assert response.json["last_check_in_label"] != "Not yet"