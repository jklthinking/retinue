"""Pull read-only node facts under an explicit account; report with an existing node token.

The operator supplies the SSH/source/account configuration. Dry run never reads
a credential or writes to the API. This tool does not install a timer, register
an identity, run a model, synchronize chat, or control a worker.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request


SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMAND = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}\.service$")
HINT = re.compile(r"^~(/[A-Za-z0-9._-]+)+$")
RUNTIMES = frozenset(("codex", "claude-code", "kimi", "hermes", "openclaw", "opencode",
                      "gemini", "github-copilot", "cursor-agent", "kiro"))

# Fixed program, never supplied by a task or by an SSH response. It runs after
# runuser and imports only the operator-selected, read-only Retinue package.
REMOTE_PROGRAM = r'''
import datetime, json, os, pathlib, pwd, sys
from node import probe, runtime_probe
try:
    params = json.loads(sys.argv[1])
    account = pwd.getpwuid(os.geteuid())
    if os.geteuid() == 0 or account.pw_name != params["probe_user"] or pathlib.Path.home().resolve() != pathlib.Path(account.pw_dir).resolve():
        raise ValueError("account-home-mismatch")
    heartbeat = probe.collect(params["node_id"], params["label"], params["services"])
    heartbeat["label"] = params["label"]
    runtimes = runtime_probe.collect(params["node_id"])
    result = {"schema_version": 1, "source": "node-runtime-probe", "privacy": "metadata",
              "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "heartbeat": heartbeat, "runtimes": runtimes}
except (Exception, SystemExit):
    print(json.dumps({"error": "fixed-node-probe-failed"}))
    sys.exit(1)
print(json.dumps(result, separators=(",", ":")))
'''


class NodePullError(RuntimeError):
    """Fixed categories only; credentials, remote stderr and response bodies stay private."""


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/") or any(c in value for c in "\r\n\0"):
        raise NodePullError("invalid-operator-path")
    return value


def _api_base(value: Any) -> str:
    if not isinstance(value, str):
        raise NodePullError("invalid-loopback-api")
    parsed = urllib.parse.urlsplit(value)
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        valid_port = parsed.port is None or 0 < parsed.port <= 65535
    except ValueError:
        loopback = valid_port = False
    if (not loopback or not valid_port or parsed.scheme not in {"http", "https"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise NodePullError("invalid-loopback-api")
    return value.rstrip("/")


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NodePullError("operator-config-unreadable") from exc
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise NodePullError("invalid-operator-config")
    if not isinstance(config.get("node_id"), str) or not SLUG.fullmatch(config["node_id"]):
        raise NodePullError("invalid-node-id")
    if config.get("interval_seconds", 900) != 900:
        raise NodePullError("node-observation-interval-must-be-15-minutes")
    config["url"] = _api_base(config.get("url"))
    token_file = config.get("token_file")
    if not isinstance(token_file, str) or not Path(token_file).is_absolute():
        raise NodePullError("existing-node-token-path-required")
    config.setdefault("label", "")
    if not isinstance(config["label"], str) or len(config["label"]) > 128 or any(ord(c) < 32 for c in config["label"]):
        raise NodePullError("invalid-node-label")
    services = config.setdefault("services", [])
    if not isinstance(services, list) or len(services) > 16 or any(not isinstance(s, str) or not UNIT.fullmatch(s) for s in services):
        raise NodePullError("invalid-service-allowlist")
    ssh_argv(config)
    return config


def ssh_argv(config: dict[str, Any]) -> list[str]:
    remote = config.get("remote")
    if not isinstance(remote, dict):
        raise NodePullError("missing-fixed-source")
    host, user = remote.get("host"), remote.get("probe_user")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", host):
        raise NodePullError("invalid-ssh-alias")
    if not isinstance(user, str) or user == "root" or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
        raise NodePullError("invalid-probe-account")
    home, workspace = _path(remote.get("probe_home")), _path(remote.get("workspace"))
    params = {"node_id": config["node_id"], "label": config.get("label", ""),
              "services": config.get("services", []), "probe_user": user}
    search = ":".join((home + "/.local/bin", home + "/.cargo/bin", home + "/bin",
                       "/usr/local/bin", "/usr/bin", "/bin"))
    command = ["/usr/sbin/runuser", "-u", user, "--", "/usr/bin/env", "-i", "HOME=" + home,
               "USER=" + user, "LOGNAME=" + user, "PATH=" + search, "PYTHONPATH=" + workspace,
               _path(remote.get("python", "/usr/bin/python3")), "-c", REMOTE_PROGRAM, json.dumps(params)]
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
    if remote.get("ssh_config"):
        argv += ["-F", _path(remote["ssh_config"])]
    return argv + [host, shlex.join(command)]


def _count(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise NodePullError("invalid-observation-count")
    return value


def _numbers(value: Any, keys: tuple[str, ...]) -> dict[str, int | float]:
    if not isinstance(value, dict):
        raise NodePullError("invalid-system-facts")
    result = {}
    for key in keys:
        if key in value:
            number = value[key]
            if not isinstance(number, (int, float)) or isinstance(number, bool) or not 0 <= number < float("inf"):
                raise NodePullError("invalid-system-facts")
            result[key] = number
    return result


def validate_snapshot(snapshot: Any, node_id: str, *, now: dt.datetime | None = None) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1 or snapshot.get("source") != "node-runtime-probe" or snapshot.get("privacy") != "metadata":
        raise NodePullError("invalid-observation-schema")
    try:
        stamp = dt.datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("missing timezone")
        age = ((now or dt.datetime.now(dt.timezone.utc)) - stamp).total_seconds()
        if age < -60 or age > 120:
            raise ValueError("old or future source")
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise NodePullError("observation-clock-or-freshness-unverified") from exc
    heartbeat, inventory = snapshot.get("heartbeat"), snapshot.get("runtimes")
    if not isinstance(heartbeat, dict) or not isinstance(inventory, dict) or heartbeat.get("id") != node_id or inventory.get("node_id") != node_id:
        raise NodePullError("observation-node-mismatch")
    facts = {"id": node_id, "uptime_seconds": _count(heartbeat.get("uptime_seconds")),
             "disk": _numbers(heartbeat.get("disk"), ("total", "used", "free", "percent")),
             "memory": _numbers(heartbeat.get("memory"), ("total", "available", "swap_total", "swap_free"))}
    for key in ("label", "hostname", "platform"):
        value = heartbeat.get(key, "")
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise NodePullError("invalid-system-label")
        facts[key] = value
    load = heartbeat.get("load")
    if not isinstance(load, list) or len(load) > 3:
        raise NodePullError("invalid-system-load")
    facts["load"] = list(_numbers(dict(enumerate(load)), tuple(range(len(load)))).values())
    services = heartbeat.get("services", [])
    if not isinstance(services, list) or len(services) > 16:
        raise NodePullError("invalid-observed-services")
    facts["services"] = []
    for item in services:
        if not isinstance(item, dict) or not isinstance(item.get("unit"), str) or not UNIT.fullmatch(item["unit"]):
            raise NodePullError("invalid-observed-services")
        if not isinstance(item.get("healthy"), bool):
            raise NodePullError("invalid-observed-services")
        status = {"unit": item["unit"], "healthy": item["healthy"], "restarts": _count(item.get("restarts", 0))}
        for key in ("active", "sub"):
            value = item.get(key, "unknown")
            if not isinstance(value, str) or not re.fullmatch(r"[a-z-]{1,32}", value):
                raise NodePullError("invalid-observed-services")
            status[key] = value
        facts["services"].append(status)
    entries = inventory.get("runtimes")
    if not isinstance(entries, list) or len(entries) > 64:
        raise NodePullError("invalid-runtime-inventory")
    runtimes, seen = [], set()
    for item in entries:
        if not isinstance(item, dict) or item.get("runtime") not in RUNTIMES or item["runtime"] in seen:
            raise NodePullError("invalid-runtime-inventory")
        if not isinstance(item.get("command"), str) or not COMMAND.fullmatch(item["command"]) or not isinstance(item.get("available"), bool):
            raise NodePullError("invalid-runtime-inventory")
        if item.get("source", "path") not in {"path", "well-known", "pin"}:
            raise NodePullError("invalid-runtime-inventory")
        seen.add(item["runtime"])
        runtimes.append({"runtime": item["runtime"], "command": item["command"], "available": item["available"], "source": item.get("source", "path")})
    data_dirs = inventory.get("data_dirs")
    inventory_body: dict[str, Any] = {"node_id": node_id, "runtimes": runtimes}
    if data_dirs is not None:
        if not isinstance(data_dirs, list) or len(data_dirs) > 64:
            raise NodePullError("invalid-data-directory-inventory")
        inventory_body["data_dirs"], seen = [], set()
        for item in data_dirs:
            if not isinstance(item, dict) or item.get("runtime") not in RUNTIMES or item["runtime"] in seen:
                raise NodePullError("invalid-data-directory-inventory")
            hint = item.get("path_hint")
            if not isinstance(hint, str) or len(hint) > 256 or not HINT.fullmatch(hint):
                raise NodePullError("invalid-data-directory-inventory")
            changed = item.get("last_changed_at")
            if changed is not None:
                try:
                    parsed = dt.datetime.fromisoformat(changed.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError("missing timezone")
                except (AttributeError, ValueError) as exc:
                    raise NodePullError("invalid-data-directory-timestamp") from exc
            seen.add(item["runtime"])
            inventory_body["data_dirs"].append({"runtime": item["runtime"], "path_hint": hint, "last_changed_at": changed})
    return {"observed_at": stamp.astimezone(dt.timezone.utc).isoformat(), "heartbeat": facts, "runtimes": inventory_body}


def pull(config: dict[str, Any]) -> dict[str, Any]:
    try:
        result = subprocess.run(ssh_argv(config), capture_output=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NodePullError("fixed-source-transport-failed") from exc
    if result.returncode != 0:
        raise NodePullError("fixed-source-account-or-probe-failed")
    if len(result.stdout) > 256 * 1024:
        raise NodePullError("fixed-source-response-too-large")
    try:
        snapshot = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError) as exc:
        raise NodePullError("fixed-source-invalid-json") from exc
    return validate_snapshot(snapshot, config["node_id"])


def read_token(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 256 or os.name != "nt" and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
                raise NodePullError("existing-node-token-file-not-private")
            token = stream.read(256).strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise NodePullError("existing-node-token-unreadable") from exc
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token):
        raise NodePullError("existing-node-token-invalid")
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NodePullError("node-api-redirect-refused")


def post(url: str, token: str, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    if endpoint not in {"/api/nodes/heartbeat", "/api/nodes/runtimes"}:
        raise NodePullError("node-api-endpoint-refused")
    request = urllib.request.Request(_api_base(url) + endpoint, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise NodePullError("node-api-response-too-large")
            result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise NodePullError("node-api-http-" + str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise NodePullError("node-api-transport-or-json-failed") from exc
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise NodePullError("node-api-response-unconfirmed")
    return result


def run_once(config: dict[str, Any], *, dry_run: bool, source=pull, api=post,
             token_reader: Callable[[str], str] = read_token) -> dict[str, Any]:
    observation = source(config)
    result = {"status": "dry-run" if dry_run else "reported", "node_id": config["node_id"],
              "observed_at": observation["observed_at"], "recommended_interval_seconds": 900,
              "runtime_count": len(observation["runtimes"]["runtimes"]),
              "runtime_ids": [item["runtime"] for item in observation["runtimes"]["runtimes"]],
              "data_dirs_checked": "data_dirs" in observation["runtimes"],
              "data_directory_count": len(observation["runtimes"].get("data_dirs", [])),
              "service_count": len(observation["heartbeat"]["services"])}
    if not dry_run:
        token = token_reader(config["token_file"])
        api(config["url"], token, "/api/nodes/heartbeat", observation["heartbeat"])
        response = api(config["url"], token, "/api/nodes/runtimes", observation["runtimes"])
        if response.get("node_id") != config["node_id"]:
            raise NodePullError("node-runtime-report-identity-unconfirmed")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--once", action="store_true")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    if args.dry_run and args.push or args.once and not args.push:
        parser.error("use --dry-run, or --once --push")
    try:
        print(json.dumps(run_once(load_config(args.config), dry_run=args.dry_run), sort_keys=True))
    except (NodePullError, OSError, ValueError, TypeError, KeyError) as exc:
        category = str(exc) if isinstance(exc, NodePullError) else "node-pull-failed-check-private-config"
        print(json.dumps({"status": category}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
