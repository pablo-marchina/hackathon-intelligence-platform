from hackathon_api.main import app


def test_w5_openapi_surface() -> None:
    paths = app.openapi()["paths"]
    required = {
        "/v1/me/preferences",
        "/v1/me/recommendations",
        "/v1/me/saved-searches",
        "/v1/me/saved-searches/{search_id}",
        "/v1/me/alerts",
        "/v1/me/alerts/{alert_id}/read",
        "/v1/me/alert-preferences",
    }
    assert required <= set(paths)
