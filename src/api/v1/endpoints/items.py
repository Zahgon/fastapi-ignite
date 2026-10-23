"""
API endpoints for Item resources
"""
import logging
import uuid
from typing import Any, Dict

from flask import Blueprint, Response, request
from pydantic import Field, TypeAdapter
from typing_extensions import Annotated
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import db_session
from src.api.openapi import operation, path_parameter, query_parameter
from src.api.responses import (
    empty_response, json_response, model_list_response, model_response
)
from src.api.validation import (
    RequestValidationError, body_errors, parse_body, validate_body,
    validate_path_param, validate_query_params
)
from src.cache import CacheBackend, cached, get_cache
from src.core.aio import run_async
from src.core.config import settings
from src.core.exceptions import HTTPException
from src.schemas.item import ItemCreate, ItemResponse, ItemUpdate
from src.services.cached_item_service import CachedItemService
from src.services.item_service import ItemService


logger = logging.getLogger(__name__)

router = Blueprint("items", __name__, url_prefix="/items")

_ITEM_ID = TypeAdapter(uuid.UUID)
_SKIP = TypeAdapter(Annotated[int, Field(ge=0)])
_LIMIT = TypeAdapter(Annotated[int, Field(ge=1, le=100)])
_ACTIVE_ONLY = TypeAdapter(bool)
_SEARCH_TERM = TypeAdapter(Annotated[str, Field(min_length=1)])

ITEM_ID_PARAMETER = path_parameter("item_id", {"type": "string", "format": "uuid"})
SKIP_PARAMETER = query_parameter(
    "skip", {"type": "integer", "minimum": 0, "default": 0}, "Number of items to skip"
)
LIMIT_PARAMETER = query_parameter(
    "limit",
    {"type": "integer", "maximum": 100, "minimum": 1, "default": 100},
    "Max number of items to return",
)


def _cache_backend() -> CacheBackend:
    generator = get_cache()
    return run_async(generator.__anext__())


@router.post("/")
@operation(
    tags=["items"],
    summary="Create a new item",
    description="Create a new item with the provided information",
    body="ItemCreate",
    response="ItemResponse",
    status_code=201,
)
def create_item() -> Response:
    """
    Create a new item
    """
    item_data = validate_body(
        ItemCreate, request.get_data(), request.headers.get("Content-Type")
    )

    with db_session() as db:
        item = run_async(ItemService.create_item(db, item_data))
        return model_response(ItemResponse, item, status=201)


@cached(ttl=settings.CACHE_TTL_SECONDS, key_prefix="item")
async def _read_item(item_id: uuid.UUID, db: AsyncSession) -> Any:
    return await ItemService.get_item(db, item_id)


@router.get("/<item_id>")
@operation(
    tags=["items"],
    summary="Get item by ID",
    description="Get detailed information about a specific item by its ID",
    parameters=[ITEM_ID_PARAMETER],
    response="ItemResponse",
)
def get_item(item_id: str) -> Response:
    """
    Get an item by ID
    """
    parsed_id = validate_path_param("item_id", _ITEM_ID, item_id)

    with db_session() as db:
        item = run_async(_read_item(item_id=parsed_id, db=db))
        return model_response(ItemResponse, item)


@cached(
    ttl=60,
    key_builder=lambda *args, **kwargs: (
        f"items:{kwargs.get('active_only')}:{kwargs.get('skip')}:{kwargs.get('limit')}"
    ),
)
async def _read_items(
    skip: int, limit: int, active_only: bool, db: AsyncSession
) -> Any:
    return await ItemService.get_items(
        db, skip=skip, limit=limit, active_only=active_only
    )


@router.get("/")
@operation(
    tags=["items"],
    summary="List items",
    description="Get a list of items with optional pagination and filtering",
    parameters=[
        SKIP_PARAMETER,
        LIMIT_PARAMETER,
        query_parameter(
            "active_only",
            {"type": "boolean", "default": False},
            "Only return active items",
        ),
    ],
    response=["ItemResponse"],
)
def list_items() -> Response:
    """
    List items with pagination
    """
    params = validate_query_params(
        request.args,
        (
            ("skip", _SKIP, False, 0),
            ("limit", _LIMIT, False, 100),
            ("active_only", _ACTIVE_ONLY, False, False),
        ),
    )

    with db_session() as db:
        items = run_async(
            _read_items(
                skip=params["skip"],
                limit=params["limit"],
                active_only=params["active_only"],
                db=db,
            )
        )
        return model_list_response(ItemResponse, items)


@router.put("/<item_id>")
@operation(
    tags=["items"],
    summary="Update item",
    description="Update an existing item's information",
    parameters=[ITEM_ID_PARAMETER],
    body="ItemUpdate",
    response="ItemResponse",
)
def update_item(item_id: str) -> Response:
    """
    Update an item
    """
    # The body is decoded first; path and body values are then validated together
    # and every failure is reported at once
    payload = parse_body(request.get_data(), request.headers.get("Content-Type"))
    errors = []
    try:
        parsed_id = validate_path_param("item_id", _ITEM_ID, item_id)
    except RequestValidationError as exc:
        errors.extend(exc.errors())
    item_data, body_failures = body_errors(ItemUpdate, payload)
    errors.extend(body_failures)
    if errors:
        raise RequestValidationError(errors)

    with db_session() as db:
        item = run_async(ItemService.update_item(db, parsed_id, item_data))
        return model_response(ItemResponse, item)


@router.delete("/<item_id>")
@operation(
    tags=["items"],
    summary="Delete item",
    description="Delete an existing item",
    parameters=[ITEM_ID_PARAMETER],
    status_code=204,
)
def delete_item(item_id: str) -> Response:
    """
    Delete an item
    """
    parsed_id = validate_path_param("item_id", _ITEM_ID, item_id)

    with db_session() as db:
        run_async(ItemService.delete_item(db, parsed_id))
        return empty_response(204)


@router.get("/search/")
@operation(
    tags=["items"],
    summary="Search items",
    description="Search for items by term in name or description",
    parameters=[
        query_parameter(
            "q", {"type": "string", "minLength": 1}, "Search term", required=True
        ),
        SKIP_PARAMETER,
        LIMIT_PARAMETER,
    ],
    response=["ItemResponse"],
)
def search_items() -> Response:
    """
    Search for items by name or description
    """
    params = validate_query_params(
        request.args,
        (
            ("q", _SEARCH_TERM, True, None),
            ("skip", _SKIP, False, 0),
            ("limit", _LIMIT, False, 100),
        ),
    )

    with db_session() as db:
        items = run_async(
            ItemService.search_items(
                db,
                search_term=params["q"],
                skip=params["skip"],
                limit=params["limit"],
            )
        )
        return model_list_response(ItemResponse, items)


@router.get("/cached/<item_id>")
@operation(
    tags=["items"],
    summary="Get item by ID (using direct cache)",
    description="Get item details using the cache backend directly",
    parameters=[ITEM_ID_PARAMETER],
    response="ItemResponse",
)
def get_cached_item(item_id: str) -> Response:
    """
    Get an item using the direct cache access pattern
    """
    parsed_id = validate_path_param("item_id", _ITEM_ID, item_id)
    cache = _cache_backend()

    with db_session() as db:
        item_data = run_async(
            CachedItemService.direct_cache_example(db, cache, parsed_id)
        )

        if not item_data:
            raise HTTPException(
                status_code=404,
                detail=f"Item with ID {parsed_id} not found",
            )

        return model_response(ItemResponse, item_data)


@router.get("/cache/clear")
@operation(
    tags=["items"],
    summary="Clear item cache",
    description="Clear all cached items to test cache invalidation",
    response=dict,
)
def clear_item_cache() -> Response:
    """
    Clear all item-related cache entries
    """
    cache = _cache_backend()

    cursor = "0"
    deleted_keys = 0

    while cursor != "0" or deleted_keys == 0:
        cursor, keys = run_async(cache.scan(cursor, "item:*", 100))

        if keys:
            deleted_keys += run_async(cache.delete(*keys))

        if cursor == "0" and deleted_keys > 0:
            break

    return json_response(
        {
            "message": f"Successfully cleared {deleted_keys} cached items",
            "deleted_count": deleted_keys,
        }
    )


@router.get("/cache/info")
@operation(
    tags=["items"],
    summary="Get cache information",
    description="Get information about the current cache configuration",
    response=dict,
)
def get_cache_info() -> Response:
    """
    Get information about the current cache configuration
    """
    backend_type = settings.CACHE_BACKEND_TYPE

    info: Dict[str, Any] = {
        "cache_backend_type": backend_type,
        "cache_ttl_seconds": settings.CACHE_TTL_SECONDS,
        "file_cache_path": (
            settings.CACHE_FILE_PATH if backend_type == "file" else None
        ),
        "redis_uri": (
            str(settings.REDIS_URI) if backend_type == "redis" else None
        ),
    }

    return json_response(info)
