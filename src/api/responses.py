"""
Response helpers

Every JSON body the application produces is rendered here so that the wire format is
identical across endpoints: compact separators, no ASCII escaping, and key order taken
from the value being serialised rather than sorted.
"""

import json
from http import HTTPStatus
from typing import Any, Callable, Iterable, List, Optional, Type

from flask import Flask, Response
from pydantic import BaseModel


JSON_CONTENT_TYPE = "application/json"


def render_json(data: Any) -> str:
    """
    Serialize a value to the JSON form used on the wire
    """
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def json_response(
    data: Any, status: int = 200, headers: Optional[dict] = None
) -> Response:
    """
    Build a JSON response; ``headers`` are emitted ahead of the entity headers
    """
    return Response(
        render_json(data),
        status=status,
        content_type=JSON_CONTENT_TYPE,
        headers=headers or {},
    )


def empty_response(status: int) -> Response:
    """
    Build a body-less response that still advertises the JSON content type
    """
    response = Response(b"", status=status)
    response.headers["Content-Type"] = JSON_CONTENT_TYPE
    return response


def model_response(model: Type[BaseModel], value: Any, status: int = 200) -> Response:
    """
    Validate a value against a response model and serialize it
    """
    return json_response(model.model_validate(value).model_dump(mode="json"), status)


def model_list_response(
    model: Type[BaseModel], values: Any, status: int = 200
) -> Response:
    """
    Validate a sequence of values against a response model and serialize it
    """
    payload: List[Any] = [
        model.model_validate(value).model_dump(mode="json") for value in values
    ]
    return json_response(payload, status)


def plain_text_response(
    body: str, status: int = 200, headers: Optional[dict] = None
) -> Response:
    """
    Build a plain-text response
    """
    return Response(
        body,
        status=status,
        content_type="text/plain; charset=utf-8",
        headers=headers or {},
    )


def html_response(body: str, status: int = 200) -> Response:
    """
    Build an HTML response
    """
    return Response(body, status=status, content_type="text/html; charset=utf-8")


class EntityHeaderOrder:
    """
    WSGI middleware emitting Content-Length ahead of Content-Type

    Werkzeug adds Content-Type before Content-Length; this API has always sent the
    length first, at the position the pair occupies, with every other header
    left where it is.
    """

    def __init__(self, wsgi_app: Callable) -> None:
        self.wsgi_app = wsgi_app

    def __call__(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        def reordering_start_response(status, headers, exc_info=None):
            names = [name.lower() for name, _ in headers]
            if "content-type" in names and "content-length" in names:
                entity = [
                    headers[names.index("content-length")],
                    headers[names.index("content-type")],
                ]
                position = min(
                    names.index("content-type"), names.index("content-length")
                )
                rest = [
                    header
                    for header, name in zip(headers, names)
                    if name not in ("content-type", "content-length")
                ]
                headers = rest[:position] + entity + rest[position:]
            return start_response(status, headers, exc_info)

        return self.wsgi_app(environ, reordering_start_response)


def register_reason_phrases(app: Flask) -> None:
    """
    Emit the standard reason phrase on every status line
    """

    @app.after_request
    def set_reason_phrase(response: Response) -> Response:
        try:
            phrase = HTTPStatus(response.status_code).phrase
        except ValueError:
            return response

        response.status = f"{response.status_code} {phrase}"
        return response
