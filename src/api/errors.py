"""
Error handlers wired into the running application

Flask renders HTML for unhandled errors and for routing failures.  The handlers below
replace that with the JSON envelopes this API has always returned, and keep the
plain-text body the server produces when a view raises.
"""
import logging
from http import HTTPStatus
from typing import Any, Optional

from flask import Flask, Response, current_app, request
from werkzeug.exceptions import HTTPException as WerkzeugHTTPException

from src.api.responses import json_response, plain_text_response
from src.api.validation import RequestValidationError
from src.core.exceptions import HTTPException


logger = logging.getLogger(__name__)

# Werkzeug adds these to every rule; only the methods a route declares belong in Allow
_IMPLICIT_METHODS = frozenset({"HEAD", "OPTIONS"})


def _jsonable(value: Any) -> Any:
    """
    Convert validation error records into JSON-compatible values
    """
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _first_matching_rule_methods(valid_methods: Any) -> Optional[str]:
    """
    List the methods declared by the first registered route whose path matches
    """
    candidates = [method for method in valid_methods or () if method not in _IMPLICIT_METHODS]
    if not candidates:
        return None

    rules = list(current_app.url_map.iter_rules())
    adapter = current_app.url_map.bind(request.host)
    matched = None
    for method in candidates:
        try:
            rule, _ = adapter.match(request.path, method=method, return_rule=True)
        except WerkzeugHTTPException:
            continue
        position = rules.index(rule)
        if matched is None or position < matched[0]:
            matched = (position, rule)

    if matched is None:
        return None

    declared = sorted(method for method in matched[1].methods or () if method not in _IMPLICIT_METHODS)
    return ", ".join(declared) or None


def register_error_handlers(app: Flask) -> None:
    """
    Register the handlers that shape the application's error responses
    """
    app.register_error_handler(RequestValidationError, handle_validation_error)
    app.register_error_handler(HTTPException, handle_http_exception)
    app.register_error_handler(WerkzeugHTTPException, handle_routing_exception)
    app.register_error_handler(Exception, handle_unexpected_exception)
    app.after_request(strip_redirect_body)


def strip_redirect_body(response: Response) -> Response:
    """
    Answer a slash redirect with a bare 307

    Routing raises its redirect before a view is reached, so Flask renders it itself:
    a 308 carrying an HTML page pointing at the canonical URL.  This API has always
    answered such a redirect with a temporary redirect and no body at all, so the
    generated page and its content type are dropped here.
    """
    if response.status_code != 308 or "Location" not in response.headers:
        return response

    response.status_code = 307
    response.status = f"307 {HTTPStatus.TEMPORARY_REDIRECT.phrase}"
    response.set_data(b"")
    response.headers.pop("Content-Type", None)
    return response


def handle_validation_error(exc: RequestValidationError) -> Response:
    """
    Report path, query and body validation failures
    """
    return json_response({"detail": _jsonable(exc.errors())}, status=422)


def handle_http_exception(exc: HTTPException) -> Response:
    """
    Report an HTTP status raised by a view
    """
    response = json_response({"detail": exc.detail}, status=exc.status_code)
    if exc.headers:
        response.headers.extend(exc.headers)
    return response


def handle_routing_exception(exc: WerkzeugHTTPException) -> Response:
    """
    Report a routing failure, such as an unknown path or an unsupported method
    """
    response = json_response({"detail": exc.name}, status=exc.code or 500)
    allow = _first_matching_rule_methods(getattr(exc, "valid_methods", None))
    if allow:
        response.headers["Allow"] = allow
    return response


def handle_unexpected_exception(exc: Exception) -> Response:
    """
    Report an exception that escaped a view
    """
    logger.exception("Unhandled exception while serving a request", exc_info=exc)
    return plain_text_response("Internal Server Error", status=500)
