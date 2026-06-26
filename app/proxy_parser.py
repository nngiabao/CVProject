from __future__ import annotations

from urllib.parse import urlparse

from app.models import ProxyConfig


SUPPORTED_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5"}


def _parse_table_row(value: str, default_scheme: str) -> ProxyConfig | None:
    columns = value.replace("\t", " ").split()
    if len(columns) < 2:
        return None

    host = columns[0]
    try:
        port = int(columns[1])
    except ValueError:
        return None

    username = columns[2] if len(columns) >= 4 else None
    password = columns[3] if len(columns) >= 4 else None
    return ProxyConfig(
        scheme=default_scheme,
        host=host,
        port=port,
        username=username,
        password=password,
    )


def _parse_colon_row(value: str, default_scheme: str) -> ProxyConfig | None:
    parts = value.split(":")
    if len(parts) != 4:
        return None

    host, port_text, username, password = (part.strip() for part in parts)
    if not host or not port_text or not username:
        return None

    try:
        port = int(port_text)
    except ValueError:
        return None

    return ProxyConfig(
        scheme=default_scheme,
        host=host,
        port=port,
        username=username,
        password=password,
    )


def parse_proxy_line(line: str, default_scheme: str = "socks5") -> ProxyConfig:
    value = line.strip()
    if not value:
        raise ValueError("Proxy line is empty")

    if "://" not in value:
        colon_proxy = _parse_colon_row(value, default_scheme)
        if colon_proxy:
            return colon_proxy
        table_proxy = _parse_table_row(value, default_scheme)
        if table_proxy:
            return table_proxy
        value = f"{default_scheme}://{value}"

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"Unsupported proxy type: {scheme}")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("Expected host and port")

    return ProxyConfig(
        scheme=scheme,
        host=parsed.hostname,
        port=parsed.port,
        username=parsed.username,
        password=parsed.password,
    )


def parse_proxy_text(text: str, default_scheme: str = "socks5") -> tuple[list[ProxyConfig], list[str]]:
    proxies: list[ProxyConfig] = []
    errors: list[str] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            proxies.append(parse_proxy_line(line, default_scheme))
        except (ValueError, TypeError) as exc:
            errors.append(f"Line {line_number}: {exc}")

    return proxies, errors
