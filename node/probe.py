"""Node probe: report machine health to the Retinue Server (stdlib only).

Collects hostname/platform/uptime/load/disk/memory plus the state of an
explicit allowlist of operating-system services, then POSTs /api/nodes/heartbeat.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform as platform_mod
import re
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from .http_client import RequestClass, open_url


def _uptime_seconds() -> int:
    if os.name == "nt":
        try:
            return int(ctypes.windll.kernel32.GetTickCount64() // 1000)
        except (AttributeError, OSError):
            return 0
    try:
        with open("/proc/uptime", encoding="ascii") as stream:
            return int(float(stream.read().split()[0]))
    except OSError:
        return 0


def _memory() -> dict[str, int]:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {
                "total": int(status.ullTotalPhys),
                "available": int(status.ullAvailPhys),
                "swap_total": max(0, int(status.ullTotalPageFile - status.ullTotalPhys)),
                "swap_free": max(0, int(status.ullAvailPageFile - status.ullAvailPhys)),
            }
        return {}
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as stream:
            for line in stream:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                    values[key] = int(rest.strip().split()[0]) * 1024
    except OSError:
        return {}
    return {
        "total": values.get("MemTotal", 0),
        "available": values.get("MemAvailable", 0),
        "swap_total": values.get("SwapTotal", 0),
        "swap_free": values.get("SwapFree", 0),
    }


def _service_state(unit: str) -> dict[str, Any]:
    if os.name == "nt":
        name = unit.removesuffix(".service")
        try:
            result = subprocess.run(
                ["sc.exe", "query", name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            match = re.search(r"STATE\s*:\s*\d+\s+(\w+)", result.stdout)
            state = match.group(1).lower() if match else "unknown"
            return {
                "unit": name,
                "active": "active" if state == "running" else "inactive",
                "sub": state,
                "restarts": 0,
                "healthy": state == "running",
            }
        except (OSError, subprocess.TimeoutExpired):
            return {
                "unit": name, "active": "unknown", "sub": "unknown",
                "restarts": 0, "healthy": False,
            }
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "--property=ActiveState,SubState,NRestarts"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        props = dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )
        return {
            "unit": unit,
            "active": props.get("ActiveState", "unknown"),
            "sub": props.get("SubState", "unknown"),
            "restarts": int(props.get("NRestarts", 0) or 0),
            "healthy": props.get("ActiveState") == "active",
        }
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"unit": unit, "active": "unknown", "sub": "unknown", "restarts": 0, "healthy": False}


def collect(node_id: str, label: str, services: list[str]) -> dict[str, Any]:
    disk_root = Path.home().anchor or "/"
    usage = shutil.disk_usage(disk_root)
    try:
        load = list(os.getloadavg())
    except (AttributeError, OSError):
        load = []
    return {
        "id": node_id,
        "label": label or node_id,
        "hostname": socket.gethostname(),
        "platform": platform_mod.platform(),
        "uptime_seconds": _uptime_seconds(),
        "load": load,
        "disk": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0,
        },
        "memory": _memory(),
        "services": [_service_state(unit) for unit in services],
    }


def push(url: str, token: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url.rstrip("/") + "/api/nodes/heartbeat",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    open_url(request, timeout=15, request_class=RequestClass.INWARD).close()
