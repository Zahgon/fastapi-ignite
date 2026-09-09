"""
OpenAPI document and interactive documentation pages
"""
import json
import re
from typing import Any, Dict, List, Optional

from pydantic.json_schema import GenerateJsonSchema

from src.core.config import settings
from src.schemas.item import ItemCreate, ItemResponse, ItemUpdate


REF_TEMPLATE = "#/components/schemas/{model}"

SCHEMA_KEY_ORDER = (
    "properties",
    "additionalProperties",
    "items",
    "anyOf",
    "$ref",
    "type",
    "format",
    "maxLength",
    "minLength",
    "maximum",
    "minimum",
    "required",
    "title",
    "description",
    "default",
)

SWAGGER_UI_JS_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
SWAGGER_UI_CSS_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
SWAGGER_UI_FAVICON_URL = "https://fastapi.tiangolo.com/img/favicon.png"
SWAGGER_UI_OAUTH2_REDIRECT_URL = "/docs/oauth2-redirect"
REDOC_JS_URL = "https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js"

# The options handed to ``SwaggerUIBundle``, emitted one per line in this order
SWAGGER_UI_PARAMETERS = {
    "dom_id": "#swagger-ui",
    "layout": "BaseLayout",
    "deepLinking": True,
    "showExtensions": True,
    "showCommonExtensions": True,
}


def _ref(name: str) -> Dict[str, str]:
    return {"$ref": REF_TEMPLATE.format(model=name)}


def _json_content(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {"application/json": {"schema": schema}}


def _success(schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if schema is None:
        return {"description": "Successful Response"}
    return {"description": "Successful Response", "content": _json_content(schema)}


def _validation_failure() -> Dict[str, Any]:
    return {
        "description": "Validation Error",
        "content": _json_content(_ref("HTTPValidationError")),
    }


def _array_of(name: str, title: str) -> Dict[str, Any]:
    return {"type": "array", "items": _ref(name), "title": title}


def _free_form_object(title: str) -> Dict[str, Any]:
    return {"additionalProperties": True, "type": "object", "title": title}


def _item_id_parameter() -> Dict[str, Any]:
    return {
        "name": "item_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "format": "uuid", "title": "Item Id"},
    }


def _query_parameter(
    name: str,
    title: str,
    schema: Dict[str, Any],
    description: str,
    required: bool = False,
) -> Dict[str, Any]:
    full_schema = {key: value for key, value in schema.items() if key != "default"}
    full_schema["description"] = description
    if "default" in schema:
        full_schema["default"] = schema["default"]
    full_schema["title"] = title
    return {
        "name": name,
        "in": "query",
        "required": required,
        "schema": full_schema,
        "description": description,
    }


def _skip_parameter() -> Dict[str, Any]:
    return _query_parameter(
        "skip",
        "Skip",
        {"type": "integer", "minimum": 0, "default": 0},
        "Number of items to skip",
    )


def _limit_parameter() -> Dict[str, Any]:
    return _query_parameter(
        "limit",
        "Limit",
        {"type": "integer", "maximum": 100, "minimum": 1, "default": 100},
        "Max number of items to return",
    )


def _operation_id(name: str, path: str, method: str) -> str:
    return re.sub(r"\W", "_", name + path) + "_" + method.lower()


# Every operation the application exposes, in registration order. Each entry is
# (path suffix below the API prefix, method, handler name, tag, summary, description,
# parameters, request body schema name, success response schema, documents 422)
_OPERATIONS = [
    (
        "/items/", "post", "create_item", "items",
        "Create a new item",
        "Create a new item with the provided information",
        [], "ItemCreate", _ref("ItemResponse"), True, "201",
    ),
    (
        "/items/", "get", "list_items", "items",
        "List items",
        "Get a list of items with optional pagination and filtering",
        [
            _skip_parameter(),
            _limit_parameter(),
            _query_parameter(
                "active_only",
                "Active Only",
                {"type": "boolean", "default": False},
                "Only return active items",
            ),
        ],
        None, None, True, "200",
    ),
    (
        "/items/{item_id}", "get", "get_item", "items",
        "Get item by ID",
        "Get detailed information about a specific item by its ID",
        [_item_id_parameter()], None, _ref("ItemResponse"), True, "200",
    ),
    (
        "/items/{item_id}", "put", "update_item", "items",
        "Update item",
        "Update an existing item's information",
        [_item_id_parameter()], "ItemUpdate", _ref("ItemResponse"), True, "200",
    ),
    (
        "/items/{item_id}", "delete", "delete_item", "items",
        "Delete item",
        "Delete an existing item",
        [_item_id_parameter()], None, None, True, "204",
    ),
    (
        "/items/search/", "get", "search_items", "items",
        "Search items",
        "Search for items by term in name or description",
        [
            _query_parameter(
                "q",
                "Q",
                {"type": "string", "minLength": 1},
                "Search term",
                required=True,
            ),
            _skip_parameter(),
            _limit_parameter(),
        ],
        None, None, True, "200",
    ),
    (
        "/items/cached/{item_id}", "get", "get_cached_item", "items",
        "Get item by ID (using direct cache)",
        "Get item details using the cache backend directly",
        [_item_id_parameter()], None, _ref("ItemResponse"), True, "200",
    ),
    (
        "/items/cache/clear", "get", "clear_item_cache", "items",
        "Clear item cache",
        "Clear all cached items to test cache invalidation",
        [], None, None, False, "200",
    ),
    (
        "/items/cache/info", "get", "get_cache_info", "items",
        "Get cache information",
        "Get information about the current cache configuration",
        [], None, None, False, "200",
    ),
    (
        "/health", "get", "health_check", "health",
        "Health Check",
        "Health check endpoint\n\nReturns a simple message to confirm the API is running",
        [], None, {}, False, "200",
    ),
    (
        "/app-info", "get", "app_info", "info",
        "App Info",
        "Application information endpoint\n\nReturns basic information about the application",
        [], None, {}, False, "200",
    ),
]


def _response_schema(handler: str, path: str, method: str, declared: Any) -> Any:
    if handler == "list_items":
        return _array_of("ItemResponse", "Response List Items Api Items  Get")
    if handler == "search_items":
        return _array_of("ItemResponse", "Response Search Items Api Items Search  Get")
    if handler == "clear_item_cache":
        return _free_form_object("Response Clear Item Cache Api Items Cache Clear Get")
    if handler == "get_cache_info":
        return _free_form_object("Response Get Cache Info Api Items Cache Info Get")
    return declared


def build_paths(prefix: str) -> Dict[str, Any]:
    """
    Build the ``paths`` section of the OpenAPI document
    """
    paths: Dict[str, Any] = {}

    for (
        suffix, method, handler, tag, summary, description,
        parameters, body, success_schema, documents_422, status_code,
    ) in _OPERATIONS:
        path = prefix + suffix
        schema = _response_schema(handler, path, method, success_schema)

        operation: Dict[str, Any] = {
            "tags": [tag],
            "summary": summary,
            "description": description,
            "operationId": _operation_id(handler, path, method),
        }
        if parameters:
            operation["parameters"] = parameters
        if body is not None:
            operation["requestBody"] = {
                "required": True,
                "content": _json_content(_ref(body)),
            }

        responses = {status_code: _success(schema)}
        if documents_422:
            responses["422"] = _validation_failure()
        operation["responses"] = responses

        paths.setdefault(path, {})[method] = operation

    return paths


class _UnsortedJsonSchema(GenerateJsonSchema):
    # Pydantic orders every generated schema alphabetically; the documented
    # order is the one the models declare, so the sorting pass is suppressed.
    def sort(
        self, value: Dict[str, Any], parent_key: Optional[str] = None
    ) -> Dict[str, Any]:
        return value


def _schema_key_rank(key: str) -> int:
    try:
        return SCHEMA_KEY_ORDER.index(key)
    except ValueError:
        return len(SCHEMA_KEY_ORDER)


def _normalise_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalise_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalised: Dict[str, Any] = {}
    for key in sorted(value, key=_schema_key_rank):
        if key == "default" and value[key] is None:
            continue
        if key == "properties":
            normalised[key] = {
                name: _normalise_schema(schema) for name, schema in value[key].items()
            }
        else:
            normalised[key] = _normalise_schema(value[key])
    return normalised


def build_component_schemas() -> Dict[str, Any]:
    """
    Build the ``components.schemas`` section of the OpenAPI document
    """
    schemas: Dict[str, Any] = {
        "ValidationError": {
            "properties": {
                "loc": {
                    "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                    "type": "array",
                    "title": "Location",
                },
                "msg": {"type": "string", "title": "Message"},
                "type": {"type": "string", "title": "Error Type"},
            },
            "type": "object",
            "required": ["loc", "msg", "type"],
            "title": "ValidationError",
        },
        "HTTPValidationError": {
            "properties": {
                "detail": {
                    "items": _ref("ValidationError"),
                    "type": "array",
                    "title": "Detail",
                }
            },
            "type": "object",
            "title": "HTTPValidationError",
        },
    }

    generator = _UnsortedJsonSchema(ref_template=REF_TEMPLATE)
    _, definitions = generator.generate_definitions(
        [
            (model, "validation", model.__pydantic_core_schema__)
            for model in (ItemCreate, ItemResponse, ItemUpdate)
        ]
    )
    for name, definition in definitions.items():
        schemas[name] = _normalise_schema(definition)

    return dict(sorted(schemas.items()))


def build_openapi_schema() -> Dict[str, Any]:
    """
    Build the complete OpenAPI document describing the application
    """
    return {
        "openapi": "3.1.0",
        "info": {
            "title": settings.PROJECT_NAME,
            "description": settings.PROJECT_DESCRIPTION,
            "version": settings.VERSION,
        },
        "paths": build_paths(settings.API_PREFIX),
        "components": {"schemas": build_component_schemas()},
    }


def render_swagger_ui(openapi_url: str, title: str) -> str:
    """
    Render the Swagger UI documentation page
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <link type="text/css" rel="stylesheet" href="{SWAGGER_UI_CSS_URL}">
    <link rel="shortcut icon" href="{SWAGGER_UI_FAVICON_URL}">
    <title>{title}</title>
    </head>
    <body>
    <div id="swagger-ui">
    </div>
    <script src="{SWAGGER_UI_JS_URL}"></script>
    <!-- `SwaggerUIBundle` is now available on the page -->
    <script>
    const ui = SwaggerUIBundle({{
        url: '{openapi_url}',
    """

    for key, value in SWAGGER_UI_PARAMETERS.items():
        html += f"{json.dumps(key)}: {json.dumps(value)},\n"

    html += (
        "oauth2RedirectUrl: window.location.origin + "
        f"'{SWAGGER_UI_OAUTH2_REDIRECT_URL}',"
    )

    html += """
    presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIBundle.SwaggerUIStandalonePreset
        ],
    })
    </script>
    </body>
    </html>
    """

    return html


def render_redoc(openapi_url: str, title: str) -> str:
    """
    Render the ReDoc documentation page
    """
    return f"""<!DOCTYPE html>
<html>
<head>
<title>{title}</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
<style>
body {{
    margin: 0;
    padding: 0;
}}
</style>
</head>
<body>
<noscript>ReDoc requires Javascript to function. Please enable it to browse the documentation.</noscript>
<redoc spec-url="{openapi_url}"></redoc>
<script src="{REDOC_JS_URL}"></script>
</body>
</html>"""
