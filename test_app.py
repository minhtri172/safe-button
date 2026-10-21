from unittest.mock import patch

# main.py calls AWS at import time, so mock boto3 first
with patch("boto3.client") as boto_client:
    boto_client.return_value.get_parameters.return_value = {"Parameters": []}
    import main

from main import app, check_in_view_data


def test_view_data_with_timestamp():
    data = check_in_view_data("2026-09-25T12:00:00+00:00")
    assert data["last_check_in_label"] == "2026-09-25 08:00:00"
    assert "check_in_deadline" in data


def test_home_page_loads_when_logged_out():
    response = app.test_client().get("/")
    assert response.status_code == 200


def test_check_in_requires_sign_in():
    response = app.test_client().post("/api/check-in")
    assert response.status_code == 401


def test_cron_check_rejects_missing_token():
    response = app.test_client().post("/api/cron-check")
    assert response.status_code == 401
