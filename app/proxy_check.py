from __future__ import annotations

import socket

from app.models import ProxyConfig


PUBLIC_IP_HOST = "api.ipify.org"


def check_proxy(proxy: ProxyConfig, timeout: float = 3) -> tuple[str, str]:
    try:
        proxy_ip = socket.gethostbyname(proxy.host)
    except OSError:
        proxy_ip = proxy.host

    if proxy.scheme == "socks5":
        return _check_socks5_proxy(proxy, proxy_ip, timeout)

    try:
        with socket.create_connection((proxy.host, proxy.port), timeout=timeout):
            return "Running", proxy_ip
    except OSError:
        return "Not running", proxy_ip


def check_http_proxy_public_ip(host: str, port: int, timeout: float = 4) -> tuple[str, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            request = (
                f"GET http://{PUBLIC_IP_HOST}/ HTTP/1.1\r\n"
                f"Host: {PUBLIC_IP_HOST}\r\n"
                "Connection: close\r\n"
                "User-Agent: GrowStoneBot/1.0\r\n"
                "\r\n"
            ).encode("ascii")
            sock.sendall(request)
            response = _recv_all(sock)
    except OSError as exc:
        return "IP check failed", str(exc)

    header, _, body = response.partition(b"\r\n\r\n")
    status_line = header.splitlines()[0].decode("iso-8859-1", errors="replace") if header else ""
    if " 200 " not in status_line:
        detail = body.decode("utf-8", errors="replace").strip()
        return "IP check failed", detail or status_line or "empty response"

    public_ip = body.decode("ascii", errors="ignore").strip()
    if not public_ip:
        return "IP check failed", "empty response body"
    return "Bridge OK", public_ip


def _check_socks5_proxy(proxy: ProxyConfig, proxy_ip: str, timeout: float) -> tuple[str, str]:
    try:
        with socket.create_connection((proxy.host, proxy.port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            if not _open_socks5_connection(sock, proxy, PUBLIC_IP_HOST, 80):
                return "Auth failed", proxy_ip
            request = (
                f"GET / HTTP/1.1\r\n"
                f"Host: {PUBLIC_IP_HOST}\r\n"
                "Connection: close\r\n"
                "User-Agent: GrowStoneBot/1.0\r\n"
                "\r\n"
            ).encode("ascii")
            sock.sendall(request)
            response = _recv_all(sock)
    except TimeoutError:
        return "IP check failed", "timeout"
    except OSError as exc:
        return "Not running", str(exc) or proxy_ip

    header, _, body = response.partition(b"\r\n\r\n")
    status_line = header.splitlines()[0].decode("iso-8859-1", errors="replace") if header else ""
    if " 200 " not in status_line:
        return "IP check failed", status_line or "empty response"

    public_ip = body.decode("ascii", errors="ignore").strip()
    if not public_ip:
        return "IP check failed", "empty response body"
    return "Running", public_ip


def _open_socks5_connection(sock: socket.socket, proxy: ProxyConfig, host: str, port: int) -> bool:
    methods = [0x00]
    if proxy.username:
        methods.append(0x02)
    sock.sendall(bytes([0x05, len(methods), *methods]))
    version, method = _recv_exact(sock, 2)
    if version != 0x05 or method == 0xFF:
        return False
    if method == 0x02 and not _authenticate_socks5(sock, proxy):
        return False

    encoded_host = host.encode("idna")
    if len(encoded_host) > 255:
        raise OSError("SOCKS5 target host is too long")
    request = (
        bytes([0x05, 0x01, 0x00, 0x03, len(encoded_host)])
        + encoded_host
        + port.to_bytes(2, "big")
    )
    sock.sendall(request)
    response = _recv_exact(sock, 4)
    if response[0] != 0x05 or response[1] != 0x00:
        raise OSError(f"SOCKS5 connect failed with code {response[1]}")
    _drain_socks5_bind_address(sock, response[3])
    return True


def _authenticate_socks5(sock: socket.socket, proxy: ProxyConfig) -> bool:
    username = (proxy.username or "").encode("utf-8")
    password = (proxy.password or "").encode("utf-8")
    if len(username) > 255 or len(password) > 255:
        return False

    sock.sendall(bytes([0x01, len(username)]) + username + bytes([len(password)]) + password)
    version, status = _recv_exact(sock, 2)
    return version == 0x01 and status == 0x00


def _recv_all(sock: socket.socket) -> bytes:
    data = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("Connection closed")
        data.extend(chunk)
    return bytes(data)


def _drain_socks5_bind_address(sock: socket.socket, atyp: int) -> None:
    if atyp == 0x01:
        _recv_exact(sock, 4)
    elif atyp == 0x03:
        _recv_exact(sock, _recv_exact(sock, 1)[0])
    elif atyp == 0x04:
        _recv_exact(sock, 16)
    else:
        raise OSError("Unsupported SOCKS5 bind address type")
    _recv_exact(sock, 2)
