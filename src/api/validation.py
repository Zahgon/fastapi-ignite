"""
Request validation helpers

Flask hands views the raw path segments, query string and request body.  These helpers
run each of them through the project's Pydantic models and raise a single
:class:`RequestValidationError` carrying the collected error records, so that a view
either receives fully validated values or does not run at all.
"""
import email.message
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Type

from pydantic import BaseModel, TypeAdapter, ValidationError


class RequestValidationError(Exception):
    """
    Raised when path, query or body values fail validation
    """

    def __init__(self, errors: Sequence[Dict[str, Any]]) -> None:
        self._errors = list(errors)
        super().__init__(self._errors)

    def errors(self) -> List[Dict[str, Any]]:
        return self._errors


def _with_loc_prefix(
    errors: Iterable[Dict[str, Any]], prefix: Tuple[Any, ...]
) -> List[Dict[str, Any]]:
    """
    Prepend a location prefix to every error record
    """
    prefixed: List[Dict[str, Any]] = []
    for error in errors:
        item = dict(error)
        item["loc"] = tuple(prefix) + tuple(item.get("loc", ()))
        prefixed.append(item)
    return prefixed


def _missing_error(loc: Tuple[Any, ...]) -> Dict[str, Any]:
    """
    Build the error record used for a value that was not supplied at all
    """
    return {"type": "missing", "loc": tuple(loc), "msg": "Field required", "input": None}


def validate_path_param(name: str, adapter: TypeAdapter, value: str) -> Any:
    """
    Validate a single path parameter
    """
    try:
        return adapter.validate_python(value)
    except ValidationError as exc:
        raise RequestValidationError(
            _with_loc_prefix(exc.errors(include_url=False), ("path", name))
        ) from None


def validate_query_params(
    args: Dict[str, str], params: Sequence[Tuple[str, TypeAdapter, bool, Any]]
) -> Dict[str, Any]:
    """
    Validate the query string against a parameter specification

    Every entry of ``params`` is a ``(name, adapter, required, default)`` tuple.  All
    parameters are checked before raising so that a request with several bad values
    reports all of them at once.
    """
    values: Dict[str, Any] = {}
    errors: List[Dict[str, Any]] = []

    for name, adapter, required, default in params:
        raw = args.get(name)
        if raw is None:
            if required:
                errors.append(_missing_error(("query", name)))
            else:
                values[name] = default
            continue
        try:
            values[name] = adapter.validate_python(raw)
        except ValidationError as exc:
            errors.extend(
                _with_loc_prefix(exc.errors(include_url=False), ("query", name))
            )

    if errors:
        raise RequestValidationError(errors)

    return values


def is_json_media_type(content_type: Optional[str]) -> bool:
    """
    Report whether a request body of this media type is to be decoded as JSON

    A body is only read as JSON when the request says it is JSON: ``application/json``
    or any ``application/*+json`` flavour.  A request that declares no media type at
    all is taken to be JSON, but a body labelled anything else (a form encoding, for
    instance) is handed to the model unparsed and fails validation there.
    """
    if not content_type:
        return True

    message = email.message.Message()
    message["content-type"] = content_type
    if message.get_content_maintype() != "application":
        return False

    subtype = message.get_content_subtype()
    return subtype == "json" or subtype.endswith("+json")


def validate_body(
    model: Type[BaseModel], raw: Optional[bytes], content_type: Optional[str] = None
) -> BaseModel:
    """
    Parse and validate a JSON request body against a Pydantic model
    """
    if raw is None or not raw.strip():
        raise RequestValidationError([_missing_error(("body",))])

    if is_json_media_type(content_type):
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RequestValidationError(
                [
                    {
                        "type": "json_invalid",
                        "loc": ("body", exc.pos),
                        "msg": "JSON decode error",
                        "input": {},
                        "ctx": {"error": exc.msg},
                    }
                ]
            ) from None
    else:
        # Not JSON as far as the request is concerned: the model sees the raw body
        payload = raw.decode("utf-8", errors="replace")

    try:
        return model.model_validate(payload, from_attributes=True)
    except ValidationError as exc:
        raise RequestValidationError(
            _with_loc_prefix(exc.errors(include_url=False), ("body",))
        ) from None
