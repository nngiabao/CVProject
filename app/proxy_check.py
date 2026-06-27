from __future__ import annotations

import socket

from app.models import ProxyConfig


def check_proxy(proxy: ProxyConfig, timeout: float = 5) -> tuple[str, str]:
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


def _check_socks5_proxy(proxy: ProxyConfig, proxy_ip: str, timeout: float) -> tuple[str, str]:
    try:
        with socket.create_connection((proxy.host, proxy.port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            methods = [0x00]
            if proxy.username:
                methods.append(0x02)
            sock.sendall(bytes([0x05, len(methods), *methods]))
            version, method = _recv_exact(sock, 2)
            if version != 0x05 or method == 0xFF:
                return "Auth failed", proxy_ip
            if method == 0x02 and not _authenticate_socks5(sock, proxy):
                return "Auth failed", proxy_ip
            return "Running", proxy_ip
    except OSError:
        return "Not running", proxy_ip


def _authenticate_socks5(sock: socket.socket, proxy: ProxyConfig) -> bool:
    username = (proxy.username or "").encode("utf-8")
    password = (proxy.password or "").encode("utf-8")
    if len(username) > 255 or len(password) > 255:
        return False

    sock.sendall(bytes([0x01, len(username)]) + username + bytes([len(password)]) + password)
    version, status = _recv_exact(sock, 2)
    return version == 0x01 and status == 0x00


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("Connection closed")
        data.extend(chunk)
    return bytes(data)
