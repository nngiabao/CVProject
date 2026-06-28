from __future__ import annotations

from typing import Optional
import select
import socket
import threading
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.models import ProxyConfig


BIND_HOST = "0.0.0.0"
LOCAL_PROXY_HOST = "127.0.0.1"
BASE_LISTEN_PORT = 19000
BUFFER_SIZE = 64 * 1024


@dataclass(frozen=True)
class RoutingSession:
    instance_index: int
    listen_host: str
    listen_port: int
    upstream: ProxyConfig

    @property
    def local_proxy(self) -> str:
        return f"{self.listen_host}:{self.listen_port}"


class RoutingService:
    def __init__(self) -> None:
        self._bridges: dict[int, LocalHttpProxyBridge] = {}

    def start(self, instance_index: int, proxy: ProxyConfig) -> RoutingSession:
        self.stop(instance_index)
        listen_port = BASE_LISTEN_PORT + instance_index
        bridge = LocalHttpProxyBridge(instance_index, LOCAL_PROXY_HOST, listen_port, proxy)
        bridge.start()
        self._bridges[instance_index] = bridge
        return bridge.session

    def stop(self, instance_index: int) -> None:
        bridge = self._bridges.pop(instance_index, None)
        if bridge is not None:
            bridge.stop()

    def stop_all(self) -> None:
        for instance_index in list(self._bridges):
            self.stop(instance_index)

    def session(self, instance_index: int) -> Optional[RoutingSession]:
        bridge = self._bridges.get(instance_index)
        return bridge.session if bridge else None

    def sessions(self) -> dict[int, RoutingSession]:
        return {index: bridge.session for index, bridge in self._bridges.items()}


class LocalHttpProxyBridge:
    def __init__(self, instance_index: int, listen_host: str, listen_port: int, upstream: ProxyConfig) -> None:
        self.session = RoutingSession(instance_index, listen_host, listen_port, upstream)
        self._stop_event = threading.Event()
        self._server: Optional[socket.socket] = None
        self._thread = threading.Thread(target=self._serve, name=f"proxy-bridge-{instance_index}", daemon=True)

    def start(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((BIND_HOST, self.session.listen_port))
        server.listen(64)
        server.settimeout(0.5)
        self._server = server
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._server is None:
                    return
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, client: socket.socket) -> None:
        with client:
            client.settimeout(15)
            try:
                request, buffered = _read_http_proxy_request(client)
                upstream = _open_upstream_socks5(self.session.upstream, request)
            except OSError as exc:
                _send_proxy_error(client, str(exc))
                return

            with upstream:
                if buffered:
                    upstream.sendall(buffered)
                else:
                    client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                _relay(client, upstream, self._stop_event)


@dataclass(frozen=True)
class SocksConnectRequest:
    atyp: int
    address_bytes: bytes
    port_bytes: bytes


def _read_http_proxy_request(client: socket.socket) -> tuple[SocksConnectRequest, bytes]:
    data = _recv_until(client, b"\r\n\r\n", 65536)
    header_text = data.decode("iso-8859-1", errors="replace")
    lines = header_text.split("\r\n")
    if not lines or not lines[0]:
        raise OSError("Empty HTTP proxy request")

    method, target, version = _split_request_line(lines[0])
    if method.upper() == "CONNECT":
        host, port = _split_host_port(target, 443)
        return _socks_request_for_host(host, port), b""

    parsed = urlsplit(target)
    if not parsed.hostname:
        host_header = next((line for line in lines[1:] if line.lower().startswith("host:")), "")
        if not host_header:
            raise OSError("HTTP proxy request is missing host")
        host, port = _split_host_port(host_header.split(":", 1)[1].strip(), 80)
        path = target or "/"
    else:
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")

    rewritten_lines = lines.copy()
    rewritten_lines[0] = f"{method} {path} {version}"
    buffered = "\r\n".join(rewritten_lines).encode("iso-8859-1", errors="replace")
    return _socks_request_for_host(host, port), buffered


def _send_proxy_error(client: socket.socket, message: str) -> None:
    body = (message or "Proxy bridge failed").encode("utf-8", errors="replace")
    response = (
        b"HTTP/1.1 502 Bad Gateway\r\n"
        b"Connection: close\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )
    try:
        client.sendall(response)
    except OSError:
        pass


def _split_request_line(line: str) -> tuple[str, str, str]:
    parts = line.split()
    if len(parts) != 3:
        raise OSError("Invalid HTTP proxy request line")
    return parts[0], parts[1], parts[2]


def _split_host_port(value: str, default_port: int) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        port_text = rest[1:] if rest.startswith(":") else ""
    else:
        host, sep, port_text = value.rpartition(":")
        if not sep:
            host, port_text = value, ""
    if not host:
        raise OSError("Proxy target host is empty")
    return host, int(port_text) if port_text else default_port


def _socks_request_for_host(host: str, port: int) -> SocksConnectRequest:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        packed = ip.packed
        atyp = 0x04 if ip.version == 6 else 0x01
        return SocksConnectRequest(atyp, packed, port.to_bytes(2, "big"))

    encoded_host = host.encode("idna")
    if len(encoded_host) > 255:
        raise OSError("Proxy target host is too long")
    return SocksConnectRequest(0x03, bytes([len(encoded_host)]) + encoded_host, port.to_bytes(2, "big"))


def _read_client_connect_request(client: socket.socket) -> SocksConnectRequest:
    version, method_count = _recv_exact(client, 2)
    if version != 0x05:
        raise OSError("Unsupported SOCKS version")
    _recv_exact(client, method_count)
    client.sendall(b"\x05\x00")

    version, command, _, atyp = _recv_exact(client, 4)
    if version != 0x05 or command != 0x01:
        raise OSError("Only SOCKS5 CONNECT is supported")

    if atyp == 0x01:
        address_bytes = _recv_exact(client, 4)
    elif atyp == 0x03:
        length = _recv_exact(client, 1)[0]
        address_bytes = bytes([length]) + _recv_exact(client, length)
    elif atyp == 0x04:
        address_bytes = _recv_exact(client, 16)
    else:
        raise OSError("Unsupported address type")

    port_bytes = _recv_exact(client, 2)
    return SocksConnectRequest(atyp, address_bytes, port_bytes)


def _open_upstream_socks5(proxy: ProxyConfig, request: SocksConnectRequest) -> socket.socket:
    upstream = socket.create_connection((proxy.host, proxy.port), timeout=15)
    try:
        methods = [0x00]
        if proxy.username:
            methods.append(0x02)
        upstream.sendall(bytes([0x05, len(methods), *methods]))
        version, method = _recv_exact(upstream, 2)
        if version != 0x05 or method == 0xFF:
            raise OSError("Upstream SOCKS5 authentication was rejected")
        if method == 0x02:
            _authenticate_upstream(upstream, proxy)

        upstream.sendall(bytes([0x05, 0x01, 0x00, request.atyp]) + request.address_bytes + request.port_bytes)
        response = _recv_exact(upstream, 4)
        if response[0] != 0x05 or response[1] != 0x00:
            raise OSError("Upstream SOCKS5 connection failed")
        _drain_socks5_bind_address(upstream, response[3])
        return upstream
    except OSError:
        upstream.close()
        raise


def _authenticate_upstream(upstream: socket.socket, proxy: ProxyConfig) -> None:
    username = (proxy.username or "").encode("utf-8")
    password = (proxy.password or "").encode("utf-8")
    if len(username) > 255 or len(password) > 255:
        raise OSError("SOCKS5 credentials are too long")
    upstream.sendall(bytes([0x01, len(username)]) + username + bytes([len(password)]) + password)
    version, status = _recv_exact(upstream, 2)
    if version != 0x01 or status != 0x00:
        raise OSError("Upstream SOCKS5 authentication failed")


def _drain_socks5_bind_address(sock: socket.socket, atyp: int) -> None:
    if atyp == 0x01:
        _recv_exact(sock, 4)
    elif atyp == 0x03:
        _recv_exact(sock, _recv_exact(sock, 1)[0])
    elif atyp == 0x04:
        _recv_exact(sock, 16)
    else:
        raise OSError("Unsupported upstream address type")
    _recv_exact(sock, 2)


def _relay(left: socket.socket, right: socket.socket, stop_event: threading.Event) -> None:
    sockets = [left, right]
    while not stop_event.is_set():
        readable, _, _ = select.select(sockets, [], [], 0.5)
        for source in readable:
            try:
                data = source.recv(BUFFER_SIZE)
            except OSError:
                return
            if not data:
                return
            target = right if source is left else left
            try:
                target.sendall(data)
            except OSError:
                return


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("Connection closed")
        data.extend(chunk)
    return bytes(data)


def _recv_until(sock: socket.socket, marker: bytes, limit: int) -> bytes:
    data = bytearray()
    while marker not in data:
        if len(data) >= limit:
            raise OSError("Request header is too large")
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("Connection closed")
        data.extend(chunk)
    return bytes(data)


