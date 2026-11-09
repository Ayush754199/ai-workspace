"""Regression tests for the SPA catch-all router.

The catch-all exists so a React deep link (/dashboard, /video-studio/editor)
survives a refresh. It previously answered *every* unknown path with
``index.html``, which:
  * made a typo'd API route look like a healthy 200 to clients,
  * let secret probes (/.env, /backend/.env) return 200,
  * made a disabled /docs claim to be served.

These tests pin the corrected contract:
  * API and metadata paths -> 404 (never the SPA shell),
  * declared client routes -> index.html,
  * unknown paths and missing assets -> 404.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import _FRONTEND_DIST, app


def _has_build() -> bool:
    return os.path.isfile(os.path.join(_FRONTEND_DIST, "index.html"))


requires_build = pytest.mark.skipif(
    not _has_build(),
    reason="frontend/dist is not built; SPA shell cannot be served",
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


API_AND_METADATA_PATHS = [
    "/docs",
    "/redoc",
    "/openapi.json",
    "/.env",
    "/backend/.env",
    "/api",
    "/api/v1/nope",
    "/api/v1/admin/nope",
    "/auth/nope",
    "/chat/nope",
    "/rooms/nope",
]


@pytest.mark.parametrize("path", API_AND_METADATA_PATHS)
def test_unknown_api_and_metadata_paths_are_404(client, path):
    """An unknown API/metadata route must never be answered with the SPA shell."""
    response = client.get(path)
    assert response.status_code == 404, f"{path} returned {response.status_code}"
    content_type = response.headers.get("content-type", "")
    assert "text/html" not in content_type, f"{path} served HTML: {content_type}"


def test_env_files_are_never_served(client):
    """Dotfile probes return 404 and never leak file contents."""
    for path in ("/.env", "/backend/.env", "/.env.local", "/frontend/.env"):
        response = client.get(path)
        assert response.status_code == 404
        body = response.text
        assert "MONGODB_URL" not in body
        assert "JWT_SECRET" not in body


def test_swagger_and_openapi_stay_disabled(client):
    """API docs remain disabled by configuration; the router does not re-enable them."""
    assert app.docs_url is None
    assert app.openapi_url is None
    assert app.redoc_url is None
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/nope",
        "/dashboard/deeply/nested",
        "/admin/nope",
        "/video-studio/nope",
        "/assets/missing.js",
        "/static/app.js",
        "/favicon-missing.png",
    ],
)
def test_unknown_paths_do_not_render_the_app_shell(client, path):
    assert client.get(path).status_code == 404


def test_root_and_health_still_work(client):
    """The fix must not shadow the real routes."""
    root = client.get("/")
    assert root.status_code == 200
    assert "Enterprise AI Workspace Backend is Running" in root.text

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


@requires_build
@pytest.mark.parametrize(
    "path",
    [
        "/login",
        "/register",
        "/dashboard",
        "/assistant",
        "/documents",
        "/images",
        "/office",
        "/research",
        "/video-studio",
        "/video-studio/editor",
        "/profile",
        "/admin",
    ],
)
def test_declared_spa_routes_serve_the_app_shell(client, path):
    """Valid React Router routes keep working (deep-link refresh)."""
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@requires_build
def test_spa_route_and_api_route_can_share_a_name(client):
    """``/documents`` is a page while ``/api/v1/documents/...`` is the API."""
    page = client.get("/documents")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")

    api = client.get("/api/v1/documents/nope")
    assert api.status_code == 404
