import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/admin/diagnosis"),
        ("DELETE", "/api/admin/reset-all-users"),
        ("GET", "/api/admin/leads"),
        ("PATCH", "/api/admin/leads/1"),
        ("POST", "/api/admin/leads/download"),
        ("POST", "/api/xlsx/import"),
        ("GET", "/api/xlsx/status/dataset_1"),
        ("GET", "/api/xlsx/datasets"),
        ("GET", "/api/notifications?user_id=admin@example.com"),
        ("GET", "/api/notifications/unread-count?user_id=admin@example.com"),
        ("PATCH", "/api/notifications/read?user_id=admin@example.com"),
        ("DELETE", "/api/notifications/1?user_id=admin@example.com"),
    ],
)
def test_admin_maintenance_routes_reject_anonymous_callers(method, path):
    response = TestClient(app).request(method, path)

    assert response.status_code == 401
