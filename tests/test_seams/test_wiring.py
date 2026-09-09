"""
Tests for the routing, serialization and error-handling seams
"""
import json
import uuid

from flask import Flask
from flask.testing import FlaskClient

from src.core.config import settings


def test_application_is_a_flask_app(test_app: Flask) -> None:
    """
    The entry point builds a Flask application
    """
    assert isinstance(test_app, Flask)


def test_routes_are_mounted_under_the_api_prefix(test_app: Flask) -> None:
    """
    Every route sits below the configured prefix, at the documented path
    """
    rules = {rule.rule for rule in test_app.url_map.iter_rules()}
    prefix = settings.API_PREFIX

    assert f"{prefix}/health" in rules
    assert f"{prefix}/app-info" in rules
    assert f"{prefix}/items/" in rules
    assert f"{prefix}/items/<item_id>" in rules
    assert f"{prefix}/items/search/" in rules
    assert f"{prefix}/items/cached/<item_id>" in rules
    assert f"{prefix}/items/cache/clear" in rules
    assert f"{prefix}/items/cache/info" in rules
    assert f"{prefix}/openapi.json" in rules
    assert f"{prefix}/docs" in rules
    assert f"{prefix}/redoc" in rules


def test_item_methods_are_registered(test_app: Flask) -> None:
    """
    The item collection and detail routes keep their method sets
    """
    methods = {}
    for rule in test_app.url_map.iter_rules():
        methods.setdefault(rule.rule, set()).update(rule.methods)

    prefix = settings.API_PREFIX
    assert {"GET", "POST"} <= methods[f"{prefix}/items/"]
    assert {"GET", "PUT", "DELETE"} <= methods[f"{prefix}/items/<item_id>"]


def test_version_prefix_is_not_mounted(client: FlaskClient) -> None:
    """
    The router is mounted at the API prefix only, never at a versioned one
    """
    response = client.get("/api/v1/items/")

    assert response.status_code == 404


def test_health_payload_is_unchanged(client: FlaskClient) -> None:
    """
    The health route answers with the documented body
    """
    response = client.get(f"{settings.API_PREFIX}/health")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.data == b'{"status":"ok","version":"1"}'


def test_app_info_payload_is_unchanged(client: FlaskClient) -> None:
    """
    The app-info route reports the project metadata in declaration order
    """
    response = client.get(f"{settings.API_PREFIX}/app-info")

    assert response.status_code == 200
    assert list(response.get_json().keys()) == ["name", "description", "version"]
    assert response.get_json() == {
        "name": settings.PROJECT_NAME,
        "description": settings.PROJECT_DESCRIPTION,
        "version": settings.VERSION,
    }


def test_json_is_compact_and_not_ascii_escaped(client: FlaskClient) -> None:
    """
    Responses are rendered without spacing and without \\uXXXX escapes
    """
    response = client.get(f"{settings.API_PREFIX}/items/cache/info")

    assert response.status_code == 200
    assert b", " not in response.data
    assert b'": ' not in response.data
    assert list(response.get_json().keys()) == [
        "cache_backend_type",
        "cache_ttl_seconds",
        "file_cache_path",
        "redis_uri",
    ]


def test_unknown_route_error_envelope(client: FlaskClient) -> None:
    """
    An unknown path answers with the JSON not-found envelope
    """
    response = client.get("/api/nope")

    assert response.status_code == 404
    assert response.headers["Content-Type"] == "application/json"
    assert response.get_json() == {"detail": "Not Found"}


def test_root_error_envelope(client: FlaskClient) -> None:
    """
    The site root is not served and answers with the same envelope
    """
    response = client.get("/")

    assert response.status_code == 404
    assert response.get_json() == {"detail": "Not Found"}


def test_wrong_method_error_envelope(client: FlaskClient) -> None:
    """
    An unsupported method answers 405 with the allowed methods listed
    """
    response = client.post(f"{settings.API_PREFIX}/health")

    assert response.status_code == 405
    assert response.headers["Content-Type"] == "application/json"
    assert response.get_json() == {"detail": "Method Not Allowed"}
    assert "GET" in response.headers["Allow"]


def test_malformed_path_parameter_is_a_validation_error(client: FlaskClient) -> None:
    """
    A path parameter that is not a UUID is reported as a validation failure
    """
    response = client.get(f"{settings.API_PREFIX}/items/not-a-uuid")

    assert response.status_code == 422
    detail = response.get_json()["detail"]
    assert detail[0]["type"] == "uuid_parsing"
    assert detail[0]["loc"] == ["path", "item_id"]
    assert detail[0]["input"] == "not-a-uuid"


def test_out_of_range_query_parameter_is_a_validation_error(client: FlaskClient) -> None:
    """
    Query parameter bounds are enforced before the handler runs
    """
    response = client.get(f"{settings.API_PREFIX}/items/?limit=1000")

    assert response.status_code == 422
    detail = response.get_json()["detail"]
    assert detail[0]["type"] == "less_than_equal"
    assert detail[0]["loc"] == ["query", "limit"]
    assert detail[0]["ctx"] == {"le": 100}


def test_missing_query_parameter_is_a_validation_error(client: FlaskClient) -> None:
    """
    A required query parameter is reported with a null input
    """
    response = client.get(f"{settings.API_PREFIX}/items/search/")

    assert response.status_code == 422
    detail = response.get_json()["detail"]
    assert detail[0]["type"] == "missing"
    assert detail[0]["loc"] == ["query", "q"]
    assert detail[0]["input"] is None


def test_unparseable_body_is_a_validation_error(client: FlaskClient) -> None:
    """
    A body that is not JSON is reported as a decode failure, not a crash
    """
    response = client.post(
        f"{settings.API_PREFIX}/items/",
        data=b'{"name":',
        content_type="application/json",
    )

    assert response.status_code == 422
    detail = response.get_json()["detail"]
    assert detail[0]["type"] == "json_invalid"
    assert detail[0]["loc"] == ["body", 8]
    assert detail[0]["msg"] == "JSON decode error"


def test_body_field_errors_are_reported_under_body(client: FlaskClient) -> None:
    """
    A missing body field is located under the body prefix
    """
    response = client.post(
        f"{settings.API_PREFIX}/items/",
        data=json.dumps({"description": "no name"}).encode(),
        content_type="application/json",
    )

    assert response.status_code == 422
    detail = response.get_json()["detail"]
    assert detail[0]["type"] == "missing"
    assert detail[0]["loc"] == ["body", "name"]


def test_rejected_extra_field_is_reported(client: FlaskClient) -> None:
    """
    The update schema still forbids unknown fields
    """
    response = client.put(
        f"{settings.API_PREFIX}/items/{uuid.uuid4()}",
        data=json.dumps({"name": "x", "bogus": 1}).encode(),
        content_type="application/json",
    )

    assert response.status_code == 422
    detail = response.get_json()["detail"]
    assert detail[0]["type"] == "extra_forbidden"
    assert detail[0]["loc"] == ["body", "bogus"]


def test_cors_preflight_is_answered(client: FlaskClient) -> None:
    """
    A preflight request short-circuits with the echoed origin
    """
    response = client.options(
        f"{settings.API_PREFIX}/items/",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert response.data == b"OK"
    assert response.headers["Access-Control-Allow-Origin"] == "http://example.com"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_cors_simple_request_is_annotated(client: FlaskClient) -> None:
    """
    A simple cross-origin request carries the wildcard allow-origin header
    """
    response = client.get(
        f"{settings.API_PREFIX}/health",
        headers={"Origin": "http://example.com"},
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_openapi_document_is_served(client: FlaskClient) -> None:
    """
    The schema document is served below the API prefix
    """
    response = client.get(f"{settings.API_PREFIX}/openapi.json")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    document = response.get_json()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["title"] == settings.PROJECT_NAME
    assert f"{settings.API_PREFIX}/items/" in document["paths"]
    assert "ItemResponse" in document["components"]["schemas"]


def test_documentation_pages_are_served(client: FlaskClient) -> None:
    """
    Both documentation pages render HTML titled after the project
    """
    docs = client.get(f"{settings.API_PREFIX}/docs")
    redoc = client.get(f"{settings.API_PREFIX}/redoc")

    assert docs.status_code == 200
    assert docs.headers["Content-Type"] == "text/html; charset=utf-8"
    assert f"<title>{settings.PROJECT_NAME} - Swagger UI</title>" in docs.get_data(as_text=True)

    assert redoc.status_code == 200
    assert redoc.headers["Content-Type"] == "text/html; charset=utf-8"
    assert f"<title>{settings.PROJECT_NAME} - ReDoc</title>" in redoc.get_data(as_text=True)


def test_read_item_still_fails_on_the_cache_key(client: FlaskClient) -> None:
    """
    Reading one item keeps raising on the unserializable cache key
    """
    response = client.get(f"{settings.API_PREFIX}/items/{uuid.uuid4()}")

    assert response.status_code == 500
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert response.data == b"Internal Server Error"


def test_exception_handlers_are_not_registered() -> None:
    """
    The exception handler registration helper is still never wired up
    """
    import main

    assert not hasattr(main, "register_exception_handlers")
