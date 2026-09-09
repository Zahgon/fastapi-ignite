"""
Response helpers

Every JSON body the application produces is rendered here so that the wire format is
identical across endpoints: compact separators, no ASCII escaping, and key order taken
from the value being serialised rather than sorted.
"""
import json
from http import HTTPStatus
from typing import Any, List, Optional, Type

from flask import Flask, Response
from pydantic import BaseModel


JSON_CONTENT_TYPE = "application/json"


def render_json(data: Any) -> str:
    """
    Serialize a value to the JSON form used on the wire
    """
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def json_response(data: Any, status: int = 200) -> Response:
    """
    Build a JSON response
    """
    return Response(
        render_json(data), status=status, content_type=JSON_CONTENT_TYPE
    )


def empty_response(status: int) -> Response:
    """
    Build a body-less response that still advertises the JSON content type
    """
    response = Response(b"", status=status)
    response.headers["Content-Type"] = JSON_CONTENT_TYPE
    return response


def model_response(
    model: Type[BaseModel], value: Any, status: int = 200
) -> Response:
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
