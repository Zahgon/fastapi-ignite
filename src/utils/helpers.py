"""
Utility helper functions
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Union

from pydantic import BaseModel


def serialize_datetime(dt: datetime) -> str:
    """
    Serialize a datetime object to ISO format string
    """
    return dt.isoformat()


def serialize_uuid(uid: uuid.UUID) -> str:
    """
    Serialize a UUID object to string
    """
    return str(uid)


def parse_json_string(json_str: str) -> Dict:
    """
    Parse a JSON string to a Python dictionary
    """
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {}


def _jsonable(value: Any) -> Any:
    """
    Convert a value into JSON-compatible primitives
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]

    return value


def model_to_dict(model: Any) -> Dict:
    """
    Convert a SQLAlchemy or Pydantic model to dictionary
    """
    if isinstance(model, BaseModel):
        return model.model_dump(mode="json")

    return {
        key: _jsonable(value)
        for key, value in vars(model).items()
        if not key.startswith("_")
    }
    

def batch_process(items: List[Any], batch_size: int = 100) -> List[List[Any]]:
    """
    Split a list of items into batches of specified size
    """
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def sanitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove None values from a dictionary
    """
    return {k: v for k, v in data.items() if v is not None}
