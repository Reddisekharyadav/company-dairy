"""Local API access, error-leak and artifact-path security tests."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from backend import app as backend_app
from ocr.screen_capture import SCREENSHOT_DIR


def _call(method: str = "GET", path: str = "/api/consent/status", headers=None):
    async def run():
        transport = httpx.ASGITransport(app=backend_app.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(run())


def test_loopback_host_and_origin_are_allowed():
    response = _call(headers={"origin": "http://127.0.0.1:8765"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:8765"


def test_dns_rebinding_host_is_rejected():
    response = _call(headers={"host": "evil.example"})
    assert response.status_code == 403
    assert response.json()["error"] == "Forbidden"


def test_cross_site_origin_is_rejected_even_for_get():
    response = _call(headers={"origin": "https://evil.example"})
    assert response.status_code == 403


def test_cross_site_referer_is_rejected_without_origin():
    response = _call(headers={"referer": "https://evil.example/page"})
    assert response.status_code == 403


def test_cross_site_fetch_metadata_is_rejected():
    response = _call(headers={"sec-fetch-site": "cross-site"})
    assert response.status_code == 403


def test_cross_site_preflight_is_rejected():
    response = _call(
        method="OPTIONS",
        headers={
            "origin": "https://evil.example",
            "access-control-request-method": "POST",
        },
    )
    assert response.status_code == 403


def test_api_errors_do_not_expose_paths_or_exception_text(monkeypatch):
    def boom():
        raise RuntimeError(r"failed opening C:\Users\HP\private\secrets.txt")

    monkeypatch.setattr("tracker.session_memory.generate_ai_context", boom)
    response = backend_app.api_ai_context()

    body = json.loads(response.body)
    assert response.status_code == 500
    assert "secrets.txt" not in json.dumps(body)
    assert "C:\\Users" not in json.dumps(body)
    assert body["error"] == "Internal error. Details are in the local WorkSense log."


def test_screenshot_endpoint_rejects_paths_outside_allowed_roots(tmp_path: Path):
    from database.models import ScreenFrame
    from database.session import SessionLocal

    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"not really an image")

    session = SessionLocal()
    try:
        frame = ScreenFrame(timestamp=__import__("datetime").datetime.now(),
                            screenshot_path=str(outside))
        session.add(frame)
        session.commit()
        frame_id = frame.id
    finally:
        session.close()

    assert backend_app._safe_screenshot_file(str(outside)) is None
    response = backend_app.serve_screenshot(frame_id)
    assert response.status_code == 404


def test_screenshot_endpoint_serves_files_inside_screenshot_directory():
    from database.models import ScreenFrame
    from database.session import SessionLocal

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    inside = SCREENSHOT_DIR / "allowed.webp"
    inside.write_bytes(b"fake image bytes")

    session = SessionLocal()
    try:
        frame = ScreenFrame(timestamp=__import__("datetime").datetime.now(),
                            screenshot_path=str(inside))
        session.add(frame)
        session.commit()
        frame_id = frame.id
    finally:
        session.close()

    assert backend_app._safe_screenshot_file(str(inside)) == inside.resolve()
    response = backend_app.serve_screenshot(frame_id)
    assert response.status_code == 200
    assert Path(response.path).resolve() == inside.resolve()


def test_invalid_workspace_is_normalized_to_company():
    scope, _date_range = backend_app._filters("not-a-workspace", "daily")
    assert scope == "company"


def test_debug_docs_are_disabled_by_default():
    assert _call(path="/openapi.json").status_code == 404
    assert _call(path="/docs").status_code == 404
    assert _call(path="/redoc").status_code == 404
