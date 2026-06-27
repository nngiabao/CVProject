from __future__ import annotations

import ctypes
import socket
from ctypes import wintypes


AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
UDP_TABLE_OWNER_PID = 1
NO_ERROR = 0
ERROR_INSUFFICIENT_BUFFER = 122


class MibTcpRowOwnerPid(ctypes.Structure):
    _fields_ = [
        ("state", wintypes.DWORD),
        ("local_addr", wintypes.DWORD),
        ("local_port", wintypes.DWORD),
        ("remote_addr", wintypes.DWORD),
        ("remote_port", wintypes.DWORD),
        ("owning_pid", wintypes.DWORD),
    ]


class MibUdpRowOwnerPid(ctypes.Structure):
    _fields_ = [
        ("local_addr", wintypes.DWORD),
        ("local_port", wintypes.DWORD),
        ("owning_pid", wintypes.DWORD),
    ]


iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)


def tcp_owner_pids() -> dict[tuple[str, int], int]:
    table = _read_table(iphlpapi.GetExtendedTcpTable, TCP_TABLE_OWNER_PID_ALL)
    count = ctypes.cast(table, ctypes.POINTER(wintypes.DWORD)).contents.value
    row_array = MibTcpRowOwnerPid * count
    rows = ctypes.cast(ctypes.addressof(table) + ctypes.sizeof(wintypes.DWORD), ctypes.POINTER(row_array)).contents
    return {(_addr(row.local_addr), _port(row.local_port)): int(row.owning_pid) for row in rows}


def udp_owner_pids() -> dict[tuple[str, int], int]:
    table = _read_table(iphlpapi.GetExtendedUdpTable, UDP_TABLE_OWNER_PID)
    count = ctypes.cast(table, ctypes.POINTER(wintypes.DWORD)).contents.value
    row_array = MibUdpRowOwnerPid * count
    rows = ctypes.cast(ctypes.addressof(table) + ctypes.sizeof(wintypes.DWORD), ctypes.POINTER(row_array)).contents
    return {(_addr(row.local_addr), _port(row.local_port)): int(row.owning_pid) for row in rows}


def _read_table(function: object, table_class: int) -> ctypes.Array[ctypes.c_ubyte]:
    size = wintypes.DWORD(0)
    result = function(None, ctypes.byref(size), False, AF_INET, table_class, 0)
    if result != ERROR_INSUFFICIENT_BUFFER:
        raise OSError(ctypes.get_last_error(), "Unable to size IP helper table")

    buffer = (ctypes.c_ubyte * size.value)()
    result = function(ctypes.byref(buffer), ctypes.byref(size), False, AF_INET, table_class, 0)
    if result != NO_ERROR:
        raise OSError(ctypes.get_last_error(), "Unable to read IP helper table")
    return buffer


def _addr(value: int) -> str:
    return socket.inet_ntoa(value.to_bytes(4, "little"))


def _port(value: int) -> int:
    return socket.ntohs(value & 0xFFFF)
