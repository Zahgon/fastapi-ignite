"""
HTTP/1.1 server for the WSGI application

Clients see the HTTP server as well as the application behind it: which requests it
refuses (with a plain-text 400 that closes the connection), how request bodies are
framed, when a connection is reused, and which scheme it takes from a forwarding
proxy.  The service has always spoken HTTP through the h11 state machine, and this
server keeps doing so for the Flask application:

* requests are parsed by h11; a request h11 rejects, including a body whose framing
  is broken, is answered ``400 Bad Request`` with a plain-text explanation, and the
  connection is closed;
* the request target is split at the first ``?``; the path is percent-decoded and
  everything after the ``?`` (a ``#`` included) is the query string;
* a header sent more than once reaches the application as its first occurrence, and a
  header whose name contains an underscore does not reach it;
* ``Expect: 100-continue`` is answered when the application first reads the body,
  pipelined requests are answered in order, and an idle keep-alive connection is
  closed after a few seconds;
* when the connecting client is a trusted proxy (``FORWARDED_ALLOW_IPS``, by default
  ``127.0.0.1``), ``X-Forwarded-Proto`` sets the URL scheme and ``X-Forwarded-For``
  the client address.
"""

import io
import ipaddress
import logging
import os
import socket
import socketserver
import sys
from email.utils import formatdate
from typing import Any, Callable, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote

import h11


logger = logging.getLogger(__name__)

#: Seconds an idle keep-alive connection is kept open between requests
KEEP_ALIVE_TIMEOUT = 5

#: Bytes read from the socket at a time
READ_SIZE = 65536

#: Body of the answer to a request that cannot be parsed
INVALID_REQUEST = b"Invalid HTTP request received."

#: Schemes a trusted proxy may announce with ``X-Forwarded-Proto``
FORWARDED_SCHEMES = frozenset({"http", "https", "ws", "wss"})

WSGIApplication = Callable[[dict, Callable[..., Any]], Iterable[bytes]]


class RequestRejected(Exception):
    """
    The client sent bytes that do not form a valid HTTP request
    """


class TrustedProxies:
    """
    The clients whose forwarding headers are believed
    """

    def __init__(self, value: str) -> None:
        self.everyone = value.strip() == "*"
        self.addresses: Set[Any] = set()
        self.networks: Set[Any] = set()
        self.names: Set[str] = set()
        for item in (part.strip() for part in value.split(",")):
            try:
                if "/" in item:
                    self.networks.add(ipaddress.ip_network(item))
                else:
                    self.addresses.add(ipaddress.ip_address(item))
            except ValueError:
                self.names.add(item)

    def __contains__(self, host: Optional[str]) -> bool:
        if self.everyone:
            return True
        if not host:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return host in self.names
        return address in self.addresses or any(address in net for net in self.networks)

    def client_host(self, forwarded_for: str) -> str:
        """
        The first untrusted address, reading ``X-Forwarded-For`` from the nearest hop
        """
        hosts = [host.strip() for host in forwarded_for.split(",")]
        if self.everyone:
            return hosts[0]
        for host in reversed(hosts):
            if host not in self:
                return host
        return hosts[0]


class RequestBody(io.RawIOBase):
    """
    ``wsgi.input``: the request body, read from the connection on demand
    """

    def __init__(self, connection: "Connection") -> None:
        super().__init__()
        self._connection = connection

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        data = self._connection.read_body(len(buffer))
        buffer[: len(data)] = data
        return len(data)


class Connection:
    """
    One client connection: parses requests with h11 and runs the application for each
    """

    def __init__(
        self,
        sock: socket.socket,
        client: Tuple[str, int],
        server: Tuple[str, int],
        app: WSGIApplication,
        proxies: TrustedProxies,
    ) -> None:
        self.sock = sock
        self.client = client
        self.server = server
        self.app = app
        self.proxies = proxies
        self.conn = h11.Connection(h11.SERVER)
        self.body = bytearray()
        self.body_complete = False
        self.waiting_for_100_continue = False
        self.response_started = False
        self.rejected = False

    # Reading

    def _next_event(self, wait: bool = True) -> Any:
        """
        The next h11 event; without ``wait`` only bytes already received are used
        """
        while True:
            try:
                event = self.conn.next_event()
            except h11.RemoteProtocolError as exc:
                raise RequestRejected(str(exc)) from exc
            if event is not h11.NEED_DATA or not wait:
                return event
            self.conn.receive_data(self.sock.recv(READ_SIZE))

    def _take_body_events(self, wait: bool) -> None:
        """
        Collect body bytes, stopping at the end of the message or when no data is ready
        """
        while not self.body_complete:
            event = self._next_event(wait)
            if isinstance(event, h11.Data):
                self.body += event.data
                if wait:
                    return
            elif isinstance(event, h11.EndOfMessage):
                self.body_complete = True
            elif isinstance(event, h11.ConnectionClosed):
                raise ConnectionResetError("client closed the connection mid-request")
            else:
                return

    def read_body(self, size: int) -> bytes:
        """
        Up to ``size`` bytes of the request body; empty once the body is exhausted
        """
        if self.waiting_for_100_continue and not self.response_started:
            self._send(
                h11.InformationalResponse(
                    status_code=100, headers=[], reason=b"Continue"
                )
            )
        self.waiting_for_100_continue = False
        if not self.body:
            try:
                self._take_body_events(wait=True)
            except RequestRejected:
                # The application sees a failed read; the client gets the 400 instead
                self.rejected = True
                raise
        data = bytes(self.body[:size])
        del self.body[:size]
        return data

    # Writing

    def _send(self, event: Any) -> None:
        data = self.conn.send(event)
        if data:
            self.sock.sendall(data)

    def reject(self) -> None:
        """
        Answer an unparseable request and close the connection
        """
        if self.conn.our_state not in (h11.IDLE, h11.SEND_RESPONSE):
            return
        headers = [
            ("content-type", "text/plain; charset=utf-8"),
            ("connection", "close"),
        ]
        self._send(
            h11.Response(status_code=400, headers=headers, reason=b"Bad Request")
        )
        self._send(h11.Data(data=INVALID_REQUEST))
        self._send(h11.EndOfMessage())

    # Requests

    def environ(self, request: h11.Request) -> dict:
        """
        The WSGI environment of a request
        """
        raw_path, _, query = request.target.partition(b"?")
        path = unquote(raw_path.decode("ascii"))
        target = request.target.decode("latin-1")
        scheme, client = "http", self.client
        headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in request.headers
        ]

        if client[0] in self.proxies:
            forwarded = dict(headers)
            proto = forwarded.get("x-forwarded-proto", "").strip()
            if proto in FORWARDED_SCHEMES:
                scheme = proto
            if "x-forwarded-for" in forwarded:
                host = self.proxies.client_host(forwarded["x-forwarded-for"])
                if host:
                    client = (host, 0)

        environ = {
            "REQUEST_METHOD": request.method.decode("ascii"),
            "SCRIPT_NAME": "",
            "PATH_INFO": path.encode("utf-8").decode("latin-1"),
            "QUERY_STRING": query.decode("latin-1"),
            "RAW_URI": target,
            "REQUEST_URI": target,
            "SERVER_NAME": self.server[0],
            "SERVER_PORT": str(self.server[1]),
            "SERVER_PROTOCOL": "HTTP/" + request.http_version.decode("ascii"),
            "REMOTE_ADDR": client[0],
            "REMOTE_PORT": str(client[1]),
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": scheme,
            "wsgi.input": io.BufferedReader(RequestBody(self)),
            "wsgi.input_terminated": True,
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        for name, value in headers:
            if "_" in name:
                # In the environment ``X_A`` and ``X-A`` would be the same key; the
                # application only reads hyphenated names, so the others are left out
                continue
            key = name.upper().replace("-", "_")
            if key not in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                key = "HTTP_" + key
            environ.setdefault(key, value)
        return environ

    def run_application(self, request: h11.Request) -> None:
        """
        Run the application for one request and write its response
        """
        environ = self.environ(request)
        close_requested = (b"connection", b"close") in request.headers
        response: List[Any] = []

        def start_response(
            status: str, headers: List[Tuple[str, str]], exc_info: Any = None
        ) -> Callable:
            if exc_info and self.response_started:
                raise exc_info[1].with_traceback(exc_info[2])
            response[:] = [status, headers]
            return lambda data: None

        try:
            result = self.app(environ, start_response)
            try:
                chunks = list(result)
            finally:
                if hasattr(result, "close"):
                    result.close()
        except Exception:
            logger.exception("Exception in WSGI application")
            response[:] = [
                "500 Internal Server Error",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Connection", "close"),
                ],
            ]
            chunks = [b"Internal Server Error"]

        if self.rejected:
            raise RequestRejected("malformed request body")

        status, headers = response
        code, _, reason = status.partition(" ")
        headers = [("date", formatdate(usegmt=True)), *headers]
        if close_requested and not any(
            name.lower() == "connection" and value == "close" for name, value in headers
        ):
            headers.append(("connection", "close"))

        # WSGI header values are latin-1 strings; they go on the wire byte for byte
        fields = [
            (name.encode("latin-1"), value.encode("latin-1")) for name, value in headers
        ]
        self.response_started = True
        self._send(
            h11.Response(
                status_code=int(code), headers=fields, reason=reason.encode("latin-1")
            )
        )
        if request.method != b"HEAD":
            for chunk in chunks:
                if chunk:
                    self._send(h11.Data(data=chunk))
        self._send(h11.EndOfMessage())

    def serve(self) -> None:
        """
        Answer requests until the connection has to be closed
        """
        served = False
        try:
            while True:
                if served:
                    self.sock.settimeout(KEEP_ALIVE_TIMEOUT)
                try:
                    event = self._next_event()
                except socket.timeout:
                    logger.debug("Closing idle connection with %s:%s", *self.client)
                    return
                self.sock.settimeout(None)
                if not isinstance(event, h11.Request):
                    return

                self.body.clear()
                self.body_complete = False
                self.response_started = False
                self.rejected = False
                waiting = self.conn.they_are_waiting_for_100_continue
                self.waiting_for_100_continue = waiting
                # Bytes that arrived with the request head are parsed before the
                # application runs, so a malformed body is refused straight away.
                self._take_body_events(wait=False)
                self.run_application(event)
                served = True

                if self.conn.our_state is h11.MUST_CLOSE:
                    return
                while not self.body_complete:
                    self._take_body_events(wait=True)
                self.conn.start_next_cycle()
        except RequestRejected as exc:
            logger.warning("Invalid HTTP request received: %s", exc)
            self.reject()
        except (ConnectionError, h11.LocalProtocolError) as exc:
            logger.debug("Connection with %s:%s ended: %s", *self.client, exc)

    def close(self) -> None:
        """
        Close the connection after the client has read everything sent to it
        """
        try:
            self.sock.shutdown(socket.SHUT_WR)
            self.sock.settimeout(KEEP_ALIVE_TIMEOUT)
            while self.sock.recv(READ_SIZE):
                pass
        except OSError as exc:
            logger.debug("Connection with %s:%s closed early: %s", *self.client, exc)
        finally:
            self.sock.close()


class _Handler(socketserver.BaseRequestHandler):
    server: "HTTPServer"

    def handle(self) -> None:
        connection = Connection(
            self.request,
            self.client_address,
            self.server.server_address,
            self.server.application,
            self.server.proxies,
        )
        try:
            connection.serve()
        finally:
            connection.close()


class HTTPServer(socketserver.ThreadingTCPServer):
    """
    Threaded HTTP/1.1 server; every connection is served on its own thread
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: Tuple[str, int], application: WSGIApplication) -> None:
        self.application = application
        # Forwarding headers are believed from the local host only, unless configured
        trusted = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
        self.proxies = TrustedProxies(trusted)
        super().__init__(address, _Handler)

    @property
    def server_port(self) -> int:
        return self.server_address[1]

    def shutdown_request(self, request: Any) -> None:
        """
        Connections are closed by their handler
        """


def serve(application: WSGIApplication, host: str, port: int) -> None:
    """
    Serve a WSGI application on ``host``:``port`` until interrupted
    """
    with HTTPServer((host, port), application) as server:
        logger.info("Serving on http://%s:%d", host, server.server_port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Shutting down")
