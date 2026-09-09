"""
Tests that drive the packaged application over a real socket
"""
import threading
from typing import Generator

import httpx
import pytest
from flask import Flask
from werkzeug.serving import make_server

from src.core.config import settings


@pytest.fixture(scope="module")
def live_server(test_app: Flask) -> Generator[str, None, None]:
    """
    Serve the application on a loopback port for the duration of the module
    """
    server = make_server("127.0.0.1", 0, test_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}{settings.API_PREFIX}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_health_bytes_over_the_wire(live_server: str) -> None:
    """
    The health route writes the documented bytes and content type
    """
    response = httpx.get(f"{live_server}/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"status":"ok","version":"1"}'


def test_unknown_route_bytes_over_the_wire(live_server: str) -> None:
    """
    An unknown path writes the JSON not-found envelope, never an HTML page
    """
    response = httpx.get(f"{live_server}/nope")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"detail":"Not Found"}'


def test_wrong_method_bytes_over_the_wire(live_server: str) -> None:
    """
    An unsupported method writes the JSON envelope and an Allow header
    """
    response = httpx.post(f"{live_server}/health")

    assert response.status_code == 405
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"detail":"Method Not Allowed"}'
    assert "GET" in response.headers["allow"]


def test_server_error_bytes_over_the_wire(live_server: str) -> None:
    """
    An unhandled failure writes plain text, not an HTML debug page
    """
    response = httpx.get(f"{live_server}/items/00000000-0000-4000-8000-000000000000")

    assert response.status_code == 500
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.content == b"Internal Server Error"


def test_validation_error_bytes_over_the_wire(live_server: str) -> None:
    """
    A malformed path parameter writes the validation envelope
    """
    response = httpx.get(f"{live_server}/items/not-a-uuid")

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    assert response.json()["detail"][0]["loc"] == ["path", "item_id"]


def test_non_ascii_is_written_as_utf8(live_server: str) -> None:
    """
    Non-ASCII output travels as raw UTF-8 rather than escape sequences
    """
    response = httpx.get(f"{live_server}/app-info")

    assert response.status_code == 200
    assert b"\\u" not in response.content


def test_cors_preflight_over_the_wire(live_server: str) -> None:
    """
    A preflight request is answered before routing, with the echoed origin
    """
    response = httpx.request(
        "OPTIONS",
        f"{live_server}/items/",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.content == b"OK"
    assert response.headers["access-control-allow-origin"] == "http://example.com"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_documentation_over_the_wire(live_server: str) -> None:
    """
    The schema document and both documentation pages answer over the wire
    """
    schema = httpx.get(f"{live_server}/openapi.json")
    docs = httpx.get(f"{live_server}/docs")
    redoc = httpx.get(f"{live_server}/redoc")

    assert schema.status_code == 200
    assert schema.json()["openapi"] == "3.1.0"

    assert docs.status_code == 200
    assert docs.headers["content-type"] == "text/html; charset=utf-8"
    assert f"{settings.PROJECT_NAME} - Swagger UI" in docs.text

    assert redoc.status_code == 200
    assert redoc.headers["content-type"] == "text/html; charset=utf-8"
    assert f"{settings.PROJECT_NAME} - ReDoc" in redoc.text
