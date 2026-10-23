"""
OpenAPI document and interactive documentation pages
"""

import inspect
import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, TypeVar

from flask import Flask
from pydantic.json_schema import GenerateJsonSchema

from src.core.config import settings
from src.schemas.item import ItemCreate, ItemResponse, ItemUpdate


REF_TEMPLATE = "#/components/schemas/{model}"

ViewT = TypeVar("ViewT", bound=Callable[..., Any])

_RULE_ARGUMENT = re.compile(r"<(?:[^:<>]+:)?([^<>]+)>")

# Werkzeug adds these to every rule; they are not part of the documented operations
_UNDOCUMENTED_METHODS = frozenset({"HEAD", "OPTIONS"})

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

SWAGGER_UI_JS_URL = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
)
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


def _title(name: str) -> str:
    return name.replace("_", " ").title()


def path_parameter(name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Describe a required path parameter
    """
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {**schema, "title": _title(name)},
    }


def query_parameter(
    name: str,
    schema: Dict[str, Any],
    description: str,
    required: bool = False,
) -> Dict[str, Any]:
    """
    Describe a query parameter; a ``default`` in ``schema`` is listed after the
    description
    """
    full_schema = {key: value for key, value in schema.items() if key != "default"}
    full_schema["description"] = description
    if "default" in schema:
        full_schema["default"] = schema["default"]
    full_schema["title"] = _title(name)
    return {
        "name": name,
        "in": "query",
        "required": required,
        "schema": full_schema,
        "description": description,
    }


def operation(
    tags: List[str],
    summary: Optional[str] = None,
    description: Optional[str] = None,
    parameters: Sequence[Dict[str, Any]] = (),
    body: Optional[str] = None,
    response: Any = None,
    status_code: int = 200,
) -> Callable[[ViewT], ViewT]:
    """
    Describe a view in the OpenAPI document

    ``body`` and ``response`` name component schemas; a response may also be a
    one-item list (an array of that schema) or ``dict`` (a free-form object). Without
    a summary or description, the view's name and docstring are used.
    """

    def decorate(view: ViewT) -> ViewT:
        view.openapi_operation = {
            "tags": list(tags),
            "summary": summary,
            "description": description,
            "parameters": list(parameters),
            "body": body,
            "response": response,
            "status_code": str(status_code),
        }
        return view

    return decorate


def _operation_id(name: str, path: str, method: str) -> str:
    return re.sub(r"\W", "_", name + path) + "_" + method.lower()


def _response_schema(response: Any, operation_id: str) -> Dict[str, Any]:
    # A response documented without a named model is titled after the operation id
    title = _title("Response_" + operation_id)
    if isinstance(response, list):
        return _array_of(response[0], title)
    if response is dict:
        return _free_form_object(title)
    if isinstance(response, str):
        return _ref(response)
    return {}


def build_paths(app: Flask) -> Dict[str, Any]:
    """
    Build the ``paths`` section of the OpenAPI document from the documented routes
    """
    paths: Dict[str, Any] = {}

    for rule in app.url_map.iter_rules():
        view = app.view_functions.get(rule.endpoint)
        spec = getattr(view, "openapi_operation", None)
        if spec is None:
            continue

        path = _RULE_ARGUMENT.sub(r"{\1}", rule.rule)
        for method in sorted((rule.methods or set()) - _UNDOCUMENTED_METHODS):
            operation_id = _operation_id(view.__name__, path, method)
            operation: Dict[str, Any] = {
                "tags": spec["tags"],
                "summary": spec["summary"] or _title(view.__name__),
                "description": spec["description"]
                or inspect.cleandoc(view.__doc__ or ""),
                "operationId": operation_id,
            }
            if spec["parameters"]:
                operation["parameters"] = spec["parameters"]
            if spec["body"] is not None:
                operation["requestBody"] = {
                    "required": True,
                    "content": _json_content(_ref(spec["body"])),
                }

            status_code = spec["status_code"]
            if status_code == "204":
                responses = {status_code: _success()}
            else:
                responses = {
                    status_code: _success(
                        _response_schema(spec["response"], operation_id)
                    )
                }
            if spec["parameters"] or spec["body"] is not None:
                responses["422"] = _validation_failure()
            operation["responses"] = responses

            paths.setdefault(path, {})[method.lower()] = operation

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


def build_openapi_schema(app: Flask) -> Dict[str, Any]:
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
        "paths": build_paths(app),
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
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
    <title>{title}</title>
    <!-- needed for adaptive design -->
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    
    <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
    
    <link rel="shortcut icon" href="{SWAGGER_UI_FAVICON_URL}">
    <!--
    ReDoc doesn't change outer page styles
    -->
    <style>
      body {{
        margin: 0;
        padding: 0;
      }}
    </style>
    </head>
    <body>
    <noscript>
        ReDoc requires Javascript to function. Please enable it to browse the documentation.
    </noscript>
    <redoc spec-url="{openapi_url}"></redoc>
    <script src="{REDOC_JS_URL}"> </script>
    </body>
    </html>
    """


def render_swagger_ui_oauth2_redirect() -> str:
    """
    Render the page Swagger UI's OAuth2 flows return to
    """
    # Taken from swagger-ui v4.14.0 dist/oauth2-redirect.html
    return """
    <!doctype html>
    <html lang="en-US">
    <head>
        <title>Swagger UI: OAuth2 Redirect</title>
    </head>
    <body>
    <script>
        'use strict';
        function run () {
            var oauth2 = window.opener.swaggerUIRedirectOauth2;
            var sentState = oauth2.state;
            var redirectUrl = oauth2.redirectUrl;
            var isValid, qp, arr;

            if (/code|token|error/.test(window.location.hash)) {
                qp = window.location.hash.substring(1).replace('?', '&');
            } else {
                qp = location.search.substring(1);
            }

            arr = qp.split("&");
            arr.forEach(function (v,i,_arr) { _arr[i] = '"' + v.replace('=', '":"') + '"';});
            qp = qp ? JSON.parse('{' + arr.join() + '}',
                    function (key, value) {
                        return key === "" ? value : decodeURIComponent(value);
                    }
            ) : {};

            isValid = qp.state === sentState;

            if ((
              oauth2.auth.schema.get("flow") === "accessCode" ||
              oauth2.auth.schema.get("flow") === "authorizationCode" ||
              oauth2.auth.schema.get("flow") === "authorization_code"
            ) && !oauth2.auth.code) {
                if (!isValid) {
                    oauth2.errCb({
                        authId: oauth2.auth.name,
                        source: "auth",
                        level: "warning",
                        message: "Authorization may be unsafe, passed state was changed in server. The passed state wasn't returned from auth server."
                    });
                }

                if (qp.code) {
                    delete oauth2.state;
                    oauth2.auth.code = qp.code;
                    oauth2.callback({auth: oauth2.auth, redirectUrl: redirectUrl});
                } else {
                    let oauthErrorMsg;
                    if (qp.error) {
                        oauthErrorMsg = "["+qp.error+"]: " +
                            (qp.error_description ? qp.error_description+ ". " : "no accessCode received from the server. ") +
                            (qp.error_uri ? "More info: "+qp.error_uri : "");
                    }

                    oauth2.errCb({
                        authId: oauth2.auth.name,
                        source: "auth",
                        level: "error",
                        message: oauthErrorMsg || "[Authorization failed]: no accessCode received from the server."
                    });
                }
            } else {
                oauth2.callback({auth: oauth2.auth, token: qp, isValid: isValid, redirectUrl: redirectUrl});
            }
            window.close();
        }

        if (document.readyState !== 'loading') {
            run();
        } else {
            document.addEventListener('DOMContentLoaded', function () {
                run();
            });
        }
    </script>
    </body>
    </html>
        """
