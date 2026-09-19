import pytest
from datetime import datetime, timezone
from main import app, check_in_view_data


@pytest.fixture
def client():
    """Configures Flask app in testing mode."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


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
    
    assert data["last_check_in_label"] != "Not yet"
    assert "check_in_deadline" in data


# ==========================================
# 2. INTEGRATION TESTS (Testing Endpoints)
# ==========================================

def test_cron_check_unauthorized(client):
    """Verify /api/cron-check rejects requests without Authorization header."""
    response = client.post("/api/cron-check")
    
    assert response.status_code == 401
    assert b"Unauthorized invocation block." in response.data