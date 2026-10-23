"""
Route resolution

Werkzeug orders rules by how specific they are, merges repeated slashes, answers HEAD
and OPTIONS for every rule and redirects only towards a trailing slash.  This API has
always resolved a request differently, and the hook below keeps that behaviour:

* routes are tried in the order they were registered, and the first one whose path and
  method both match handles the request (method names are case-sensitive);
* a route whose path matches but whose method does not answers 405, listing only the
  methods that route declares (HEAD and OPTIONS are not implied);
* when nothing matches, a path that would match with its trailing slash added or
  removed is redirected there with a bare 307 that keeps the query string;
* anything else is a 404.
"""

import re
from typing import Dict, List, Optional, Pattern, Set, Tuple
from urllib.parse import quote, unquote

from flask import Flask, Response, current_app, request
from werkzeug.exceptions import NotFound
from werkzeug.datastructures import Headers
from werkzeug.routing import Rule

from src.core.exceptions import HTTPException


_PLACEHOLDER = re.compile(r"<(?:[^:<>]+:)?([^<>]+)>")
_IMPLICIT_METHODS = frozenset({"HEAD", "OPTIONS"})
_LOCATION_SAFE = ":/%#?=@[]!$&'()*+,;"
_DEFAULT_PORTS = {"http": "80", "https": "443", "ws": "80", "wss": "443"}

_Route = Tuple[Rule, Pattern[str], Set[str]]


def answers_head(view):
    """
    Mark a view whose route also answers HEAD requests
    """
    view.answers_head = True
    return view


def _compile(rule: str) -> Pattern[str]:
    """
    Turn a rule string into a regular expression matching whole paths
    """
    parts: List[str] = []
    position = 0
    for match in _PLACEHOLDER.finditer(rule):
        parts.append(re.escape(rule[position : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        position = match.end()
    parts.append(re.escape(rule[position:]))
    return re.compile("".join(parts))


def _routes(app: Flask) -> List[_Route]:
    routes = app.extensions.get("ordered_routes")
    if routes is None:
        routes = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "static":
                continue
            view = app.view_functions[rule.endpoint]
            methods = set(rule.methods or ()) - _IMPLICIT_METHODS
            if getattr(view, "answers_head", False):
                methods.add("HEAD")
            routes.append((rule, _compile(rule.rule), methods))
        app.extensions["ordered_routes"] = routes
    return routes


def request_method() -> str:
    """
    The request method exactly as the client sent it

    Werkzeug upper-cases ``request.method``, but method names are case-sensitive: a
    lower-case ``get`` is not ``GET`` and matches no route.
    """
    return request.environ.get("REQUEST_METHOD", "GET")


def _request_path() -> str:
    """
    The decoded request path exactly as the client sent it
    """
    raw = request.environ.get("RAW_URI") or request.environ.get("REQUEST_URI")
    if not raw:
        return request.path
    return unquote(raw.split("?", 1)[0])


class _RedirectResponse(Response):
    """
    A redirect whose ``Location`` goes out exactly as built

    Werkzeug would rewrite the URL as an IRI on the way out, lower-casing the host the
    client sent; the redirect has always echoed it unchanged.
    """

    def get_wsgi_headers(self, environ: dict) -> Headers:
        headers = super().get_wsgi_headers(environ)
        headers["Location"] = self.headers["Location"]
        return headers


def _base_url() -> str:
    """
    Scheme and authority of the request: the Host header as sent, or else the address
    the server listens on (with the scheme's default port left out)
    """
    scheme = request.scheme
    if "Host" in request.headers:
        return f"{scheme}://{request.headers['Host']}"
    host, port = request.environ["SERVER_NAME"], request.environ["SERVER_PORT"]
    if port == _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _redirect(path: str) -> Response:
    url = _base_url() + path
    query = request.environ.get("QUERY_STRING", "")
    if query:
        url += "?" + query
    response = _RedirectResponse(b"", status=307)
    response.headers = Headers(
        [("Content-Length", "0"), ("Location", quote(url, safe=_LOCATION_SAFE))]
    )
    return response


def resolve_route() -> Optional[Response]:
    """
    Pick the route for the current request, before Werkzeug's own match is used
    """
    app = current_app._get_current_object()
    path = _request_path()
    method = request_method()
    partial: Optional[_Route] = None

    for route in _routes(app):
        rule, pattern, methods = route
        match = pattern.fullmatch(path)
        if match is None:
            continue
        if method in methods:
            arguments: Dict[str, str] = match.groupdict()
            if request.url_rule is rule and request.view_args == arguments:
                return None
            return app.ensure_sync(app.view_functions[rule.endpoint])(**arguments)
        if partial is None:
            partial = route

    if partial is not None:
        raise HTTPException(
            status_code=405,
            detail="Method Not Allowed",
            headers={"Allow": ", ".join(sorted(partial[2]))},
        )

    if path != "/":
        alternative = path[:-1] if path.endswith("/") else path + "/"
        if any(pattern.fullmatch(alternative) for _, pattern, _ in _routes(app)):
            return _redirect(alternative)

    raise NotFound()


def register_route_resolution(app: Flask) -> None:
    """
    Resolve every request with the precedence described above
    """
    app.url_map.merge_slashes = False
    app.before_request(resolve_route)
