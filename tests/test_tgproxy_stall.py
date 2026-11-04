"""tgproxy.make_transport: a SOCKS5 proxy that accepts the connection and stays silent no longer hangs a request
forever (httpx has no timeout of its own for the handshake), and a working proxy still works through the limit."""
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

import tgproxy


def _listen():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    return srv, srv.getsockname()[1]


@pytest.fixture
def silent_proxy():
    srv, port = _listen()
    held = []
    stop = threading.Event()

    def serve():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            held.append(conn)                       # read nothing back, answer nothing

    threading.Thread(target=serve, daemon=True).start()
    yield port
    stop.set()
    for c in held:
        c.close()
    srv.close()


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError
        data += chunk
    return data


def _pipe(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


@pytest.fixture
def working_proxy():
    """A minimal SOCKS5 proxy (no auth, CONNECT by IPv4 or domain name)."""
    srv, port = _listen()

    def handle(conn):
        n = _recv_exact(conn, 2)[1]
        _recv_exact(conn, n)
        conn.sendall(b"\x05\x00")
        ver, cmd, _, atyp = _recv_exact(conn, 4)
        if atyp == 1:
            host = socket.inet_ntoa(_recv_exact(conn, 4))
        else:
            host = _recv_exact(conn, _recv_exact(conn, 1)[0]).decode()
        dport = struct.unpack(">H", _recv_exact(conn, 2))[0]
        up = socket.create_connection((host, dport))
        conn.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack(">H", 0))
        threading.Thread(target=_pipe, args=(conn, up), daemon=True).start()
        _pipe(up, conn)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield port
    srv.close()


@pytest.fixture
def web():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


@pytest.mark.parametrize("scheme", ["socks5", "socks5h"])
def test_silent_socks_proxy_times_out(silent_proxy, scheme):
    tr_ = tgproxy.make_transport(f"{scheme}://127.0.0.1:{silent_proxy}", stall_timeout=0.5)
    started = time.monotonic()
    with httpx.Client(transport=tr_, timeout=httpx.Timeout(5.0, connect=5.0)) as client:
        with pytest.raises(httpx.TimeoutException):
            client.get("http://127.0.0.1:9/")
    assert time.monotonic() - started < 3


def test_silent_proxy_with_auth_times_out(silent_proxy):
    tr_ = tgproxy.make_transport(f"socks5h://user:pass@127.0.0.1:{silent_proxy}", stall_timeout=0.5)
    with httpx.Client(transport=tr_, timeout=5.0) as client:
        with pytest.raises(httpx.TimeoutException):
            client.get("http://example.invalid/")


def test_working_socks_proxy_still_works(working_proxy, web):
    tr_ = tgproxy.make_transport(f"socks5://127.0.0.1:{working_proxy}", stall_timeout=0.5)
    with httpx.Client(transport=tr_, timeout=5.0) as client:
        for _ in range(3):                                   # reused connection too
            assert client.get(f"http://127.0.0.1:{web}/").json() == {"ok": True}


def test_no_proxy_means_direct():
    assert tgproxy.make_transport(None) is None
    assert tgproxy.make_transport("") is None


def test_http_proxy_is_left_as_is():
    assert isinstance(tgproxy.make_transport("http://127.0.0.1:3128"), httpx.HTTPTransport)
