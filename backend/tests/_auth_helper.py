from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import init_db


def login_as_admin(client: TestClient) -> dict:
    """Ensure DB initialized, authenticate TestClient as bootstrap admin, and set Bearer header."""
    init_db()
    settings = get_settings()
    res = client.post(
        "/api/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert res.status_code == 200, f"Admin login failed: {res.status_code} {res.text}"
    data = res.json()
    client.headers.update({"Authorization": f"Bearer {data['access_token']}"})
    return data
