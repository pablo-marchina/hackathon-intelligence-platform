from fastapi.testclient import TestClient
from hackathon_api.main import app


def test_security_headers_are_present() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_admin_endpoints_are_disabled_without_key() -> None:
    response = TestClient(app).get("/admin/review-queue")
    assert response.status_code == 503
    assert response.json()["detail"] == "admin endpoints are disabled"


def test_oversized_request_is_rejected_before_handler() -> None:
    client = TestClient(app)
    response = client.post(
        "/does-not-exist",
        headers={"content-length": "999999999"},
        content=b"x",
    )
    assert response.status_code == 413
