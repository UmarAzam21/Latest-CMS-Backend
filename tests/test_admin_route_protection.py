import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/admin/diagnosis"),
        ("DELETE", "/api/admin/reset-all-users"),
    ],
)
def test_admin_maintenance_routes_reject_anonymous_callers(method, path):
    response = TestClient(app).request(method, path)

    assert response.status_code == 401
