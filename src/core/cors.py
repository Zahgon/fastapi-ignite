"""
Cross-origin resource sharing support

Flask has no cross-origin handling of its own, so the request hooks below add the
headers the API has always sent: preflight requests are answered before routing, and
every other response carries the origin and credential headers.
"""
from typing import Dict, Optional, Sequence

from flask import Flask, Response, g, request

from src.api.responses import plain_text_response


ALL_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")

SAFELISTED_HEADERS = {"Accept", "Accept-Language", "Content-Language", "Content-Type"}


def register_cors(
    app: Flask,
    allow_origins: Sequence[str] = (),
    allow_credentials: bool = False,
    allow_methods: Sequence[str] = ("GET",),
    allow_headers: Sequence[str] = (),
    expose_headers: Sequence[str] = (),
    max_age: int = 600,
) -> None:
    """
    Install the cross-origin request hooks on an application
    """
    allow_all_origins = "*" in allow_origins
    allow_all_headers = "*" in allow_headers
    preflight_explicit_allow_origin = not allow_all_origins or allow_credentials

    resolved_methods = (
        ALL_METHODS if "*" in allow_methods else tuple(allow_methods)
    )
    resolved_headers = sorted(
        SAFELISTED_HEADERS | {header for header in allow_headers if header != "*"}
    )

    simple_headers: Dict[str, str] = {}
    if allow_all_origins:
        simple_headers["Access-Control-Allow-Origin"] = "*"
    if allow_credentials:
        simple_headers["Access-Control-Allow-Credentials"] = "true"
    if expose_headers:
        simple_headers["Access-Control-Expose-Headers"] = ", ".join(expose_headers)

    preflight_headers: Dict[str, str] = {}
    if preflight_explicit_allow_origin:
        preflight_headers["Vary"] = "Origin"
    else:
        preflight_headers["Access-Control-Allow-Origin"] = "*"
    preflight_headers["Access-Control-Allow-Methods"] = ", ".join(resolved_methods)
    preflight_headers["Access-Control-Max-Age"] = str(max_age)
    if resolved_headers and not allow_all_headers:
        preflight_headers["Access-Control-Allow-Headers"] = ", ".join(resolved_headers)
    if allow_credentials:
        preflight_headers["Access-Control-Allow-Credentials"] = "true"

    def is_allowed_origin(origin: str) -> bool:
        return allow_all_origins or origin in allow_origins

    @app.before_request
    def handle_preflight() -> Optional[Response]:
        if request.method != "OPTIONS":
            return None
        if "Access-Control-Request-Method" not in request.headers:
            return None

        origin = request.headers.get("Origin")
        if origin is None:
            return None

        headers = dict(preflight_headers)
        failures = []

        if is_allowed_origin(origin):
            if preflight_explicit_allow_origin:
                headers["Access-Control-Allow-Origin"] = origin
        else:
            failures.append("origin")

        if request.headers["Access-Control-Request-Method"] not in resolved_methods:
            failures.append("method")

        requested_headers = request.headers.get("Access-Control-Request-Headers")
        if allow_all_headers and requested_headers is not None:
            headers["Access-Control-Allow-Headers"] = requested_headers
        elif requested_headers is not None:
            for header in requested_headers.split(","):
                if header.strip() not in resolved_headers:
                    failures.append("headers")
                    break

        g.cors_preflight = True

        if failures:
            return plain_text_response(
                "Disallowed CORS " + ", ".join(failures), status=400, headers=headers
            )

        return plain_text_response("OK", status=200, headers=headers)

    @app.after_request
    def add_cors_headers(response: Response) -> Response:
        # A preflight response is answered in full above; the simple-request
        # headers must not be layered on top of it
        if g.get("cors_preflight", False):
            return response

        origin = request.headers.get("Origin")
        if origin is None:
            return response

        for name, value in simple_headers.items():
            response.headers[name] = value

        if allow_all_origins and "Cookie" in request.headers:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers.add("Vary", "Origin")
        elif not allow_all_origins and is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers.add("Vary", "Origin")

        return response
