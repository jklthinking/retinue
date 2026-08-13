"""Enrollment: render or install the schedule for the node duties.

A managed node runs up to three duties on a schedule: the infrastructure
heartbeat and the agent-CLI inventory hourly, and the privacy-scoped session
index daily (the operator's cadence, fixed here).  Nothing schedules them
until an operator enrolls the node with ``retinue-node enroll``.  The
operator chooses the subset explicitly with ``--duties`` (default: all
three); a node whose session collection is handled centrally enrolls with
``--duties heartbeat,runtimes`` and needs no session configuration at all.
The install target is always chosen explicitly with ``--target`` — never
guessed from the host:

    linux-system  systemd service + timer units under /etc/systemd/system,
                  for a node whose operator has privilege
    linux-user    the user-scoped equivalent under the enrolling account's
                  ~/.config/systemd/user, for an account with no privilege
                  escalation
    windows       scheduled tasks created with schtasks, assuming only
                  Python and git on the node

Render mode is the default and is mandatory for review: ``render`` prints
exactly what installation would write or run, byte for byte, and changes
nothing.  ``--install`` is a separate, explicit action — the only path that
writes files or activates schedules.

Idempotence: every artifact has a fixed name and installation replaces
rather than appends (unit files are rewritten atomically; schtasks runs
with ``/F``), so enrolling twice leaves one schedule, not two, and
re-enrolling after an upgrade updates the schedule in place.

Configuration comes from arguments or the environment (arguments win); see
``node.cli`` for the shared ``RETINUE_*`` variables.  The two credential
kinds are not interchangeable: the node token (``--token-file``) is a
heartbeat-only credential bound to one infrastructure node, while session
sync is attributed to an actor and must authenticate with an actor API
token, supplied separately via ``--actor-token-file``.  The session duty
also needs ``--runtime``, ``--source``, and ``--actor``.  Requesting the
session duty without a readable, non-empty actor token file is refused
before anything is written — a unit that is certain to fail must never
reach a node's disk.  Missing configuration, an unreadable or empty token
file, or an unsupported target is refused the same way — never a partial
install.  Paths in rendered artifacts come from that configuration or are
derived at run time (the interpreter is ``sys.executable``); no machine
path is hard-coded.

Path-based deployments: on a node where the package cannot be installed
(no pip, no venv, a managed interpreter), the package can be copied onto
disk and found through ``PYTHONPATH`` instead.  ``--package-path`` (or
``RETINUE_PACKAGE_PATH``) records that import path in the rendered
artifacts — an ``Environment=PYTHONPATH=...`` line in each systemd service
and a ``cmd /c "set PYTHONPATH=... && ..."`` wrapper in each Windows task —
so the schedule runs the duties from the on-disk copy.  Without it the
artifacts render exactly as before, byte for byte.

Interpreter pins: the interpreter rendered into the schedule is derived at
run time (``sys.executable``) unless the operator has pinned one for this
node in the node-local pin file (``node.runtime_pins``).  A pin exists for
machines where the default interpreter cannot run the duties — one whose
venv support is absent, for example, next to a second interpreter that
works.  The pin belongs to the node: it lives in node-local configuration,
never in a shared profile, and the only place it is written is the node's
own schedule artifacts.  With no pin, rendering is byte-identical to the
unpinned derivation.  A pin that points at a file that does not exist is
refused before anything is written — a unit certain to fail must never
reach a node's disk.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import runtime_pins

TARGETS = ("linux-system", "linux-user", "windows")

SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
USER_UNIT_DIR = Path(".config/systemd/user")  # relative to the enrolling user's home

DUTY_HEARTBEAT = "heartbeat"
DUTY_RUNTIMES = "runtimes"
DUTY_SESSIONS = "sessions"

ALL_DUTY_KEYS = (DUTY_HEARTBEAT, DUTY_RUNTIMES, DUTY_SESSIONS)


def parse_duty_selection(value: str | None) -> tuple[str, ...]:
    """The ``--duties`` value as ordered, validated duty keys.  Absent means
    all three; a subset is always the operator's explicit choice."""
    if value is None or not value.strip():
        return ALL_DUTY_KEYS
    requested = [part.strip() for part in value.split(",") if part.strip()]
    for key in requested:
        if key not in ALL_DUTY_KEYS:
            raise SystemExit(f"未知职责: {key}(可选: {', '.join(ALL_DUTY_KEYS)})")
    if not requested:
        raise SystemExit(f"--duties 不能为空(可选: {', '.join(ALL_DUTY_KEYS)})")
    return tuple(key for key in ALL_DUTY_KEYS if key in requested)

HOURLY = "hourly"
DAILY = "daily"


@dataclass(frozen=True)
class EnrollConfig:
    """Everything an enrollment needs; no value names a machine by default."""

    node: str
    url: str
    token_file: str  # node token: heartbeat and inventory duties only
    actor_token_file: str  # actor API token: session duty only; "" when not selected
    runtime: str
    source: str
    actor: str
    privacy: str
    duty_keys: tuple[str, ...]  # the operator's explicit subset of ALL_DUTY_KEYS
    interpreter: str  # the node's pin when set, else derived at run time
    package_path: str = ""  # on-disk package location (PYTHONPATH); "" = installed


@dataclass(frozen=True)
class Duty:
    key: str  # unit file and task name suffix
    description: str
    args: tuple[str, ...]  # node.cli subcommand plus its own arguments
    cadence: str  # HOURLY or DAILY


def duties(config: EnrollConfig) -> tuple[Duty, ...]:
    """The scheduled duties, filtered to the operator's selection.  Shared
    settings ride in unit Environment lines and as explicit arguments in
    the Windows task command."""
    known = (
        Duty(DUTY_HEARTBEAT, "infrastructure heartbeat", ("heartbeat",), HOURLY),
        Duty(DUTY_RUNTIMES, "agent CLI inventory", ("runtimes",), HOURLY),
        Duty(
            DUTY_SESSIONS,
            "privacy-scoped session index",
            (
                "sync-sessions",
                "--runtime", config.runtime,
                "--source", config.source,
                "--actor", config.actor,
                "--privacy", config.privacy,
            ),
            DAILY,
        ),
    )
    return tuple(duty for duty in known if duty.key in config.duty_keys)


def _exec_line(config: EnrollConfig, duty: Duty) -> str:
    argv = [config.interpreter, "-m", "node.cli", *duty.args]
    return " ".join(shlex.quote(part) for part in argv)


def _token_environment(config: EnrollConfig, duty: Duty) -> str:
    """Each duty names the credential kind it actually authenticates with:
    the session duty carries the actor API token, the node duties the
    heartbeat-only node token."""
    if duty.key == DUTY_SESSIONS:
        return f"Environment=RETINUE_ACTOR_TOKEN_FILE={config.actor_token_file}"
    return f"Environment=RETINUE_NODE_TOKEN_FILE={config.token_file}"


def _service_unit(config: EnrollConfig, duty: Duty) -> str:
    environment = [
        f"Environment=RETINUE_SERVER_URL={config.url}",
        f"Environment=RETINUE_NODE_ID={config.node}",
    ]
    if config.package_path:
        # Path-based deployment: the package is on disk, not installed, so
        # the duty finds it through PYTHONPATH.
        environment.append(f"Environment=PYTHONPATH={config.package_path}")
    environment.append(_token_environment(config, duty))
    lines = "\n".join(environment)
    return f"""[Unit]
Description=Retinue node {duty.description} (managed node duty)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
{lines}
ExecStart={_exec_line(config, duty)}
"""


# A fleet is the point of this project, so the timers must not fire in lockstep.
# OnCalendar alone puts every node on the same second — the top of the hour, or
# midnight — against a server that serializes its writes. Existing managed-node
# units use the same jitter convention. An hourly duty gets a small window; a
# daily one can afford a much wider one, since it has a whole day of slack.
_JITTER = {HOURLY: "5min", DAILY: "30min"}


def _timer_unit(duty: Duty) -> str:
    return f"""[Unit]
Description=Run the Retinue node {duty.description} {duty.cadence}

[Timer]
OnCalendar={duty.cadence}
RandomizedDelaySec={_JITTER[duty.cadence]}
Persistent=true

[Install]
WantedBy=timers.target
"""


def linux_unit_dir(system_scope: bool) -> Path:
    if system_scope:
        return SYSTEM_UNIT_DIR
    return Path.home() / USER_UNIT_DIR


def linux_files(config: EnrollConfig, unit_dir: Path) -> list[tuple[Path, str]]:
    """Fixed-name unit files for the selected duties, in deterministic order."""
    files: list[tuple[Path, str]] = []
    for duty in duties(config):
        files.append((unit_dir / f"retinue-node-{duty.key}.service", _service_unit(config, duty)))
        files.append((unit_dir / f"retinue-node-{duty.key}.timer", _timer_unit(duty)))
    return files


def _systemctl(system_scope: bool) -> list[str]:
    return ["systemctl"] if system_scope else ["systemctl", "--user"]


def linux_activation(system_scope: bool, duty_keys: tuple[str, ...]) -> list[list[str]]:
    """Activation replaces in place: daemon-reload picks up rewritten units
    and enable --now on fixed-name timers never creates a second schedule.
    Only the selected timers are enabled."""
    timers = [f"retinue-node-{key}.timer" for key in duty_keys]
    return [
        [*_systemctl(system_scope), "daemon-reload"],
        [*_systemctl(system_scope), "enable", "--now", *timers],
    ]


def windows_task_name(duty: Duty) -> str:
    return f"Retinue Node {duty.key.capitalize()}"


def windows_task_argv(config: EnrollConfig, duty: Duty) -> list[str]:
    """One schtasks command per duty; /F replaces an existing task of the
    same fixed name, so re-enrollment updates in place instead of adding."""
    schedule = "HOURLY" if duty.cadence == HOURLY else "DAILY"
    common = ["--node", config.node, "--url", config.url]
    if duty.key == DUTY_SESSIONS:
        common += ["--actor-token-file", config.actor_token_file]
    else:
        common += ["--token-file", config.token_file]
    argv = [config.interpreter, "-m", "node.cli", *duty.args, *common]
    if config.package_path:
        # Path-based deployment: scheduled tasks carry no environment block,
        # so the import path rides in the command line itself.
        inner = subprocess.list2cmdline(argv)
        run = subprocess.list2cmdline(
            ["cmd", "/c", f"set PYTHONPATH={config.package_path} && {inner}"]
        )
    else:
        run = subprocess.list2cmdline(argv)
    return [
        "schtasks", "/Create",
        "/TN", windows_task_name(duty),
        "/SC", schedule,
        "/TR", run,
        "/F",
    ]


def render(config: EnrollConfig, target: str) -> str:
    """Exactly what installation would write or run, byte for byte."""
    if target not in TARGETS:
        raise SystemExit(f"不支持的安装目标: {target}(可选: {', '.join(TARGETS)})")
    lines = [
        f"retinue-node enroll --target {target}"
        " — render only; nothing written or activated",
        "",
    ]
    if target == "windows":
        for duty in duties(config):
            lines.append(f"--- scheduled task: {windows_task_name(duty)} ({duty.cadence}) ---")
            lines.append(" ".join(shlex.quote(a) for a in windows_task_argv(config, duty)))
            lines.append("")
    else:
        system_scope = target == "linux-system"
        for path, content in linux_files(config, linux_unit_dir(system_scope)):
            lines.append(f"--- {path} ---")
            lines.append(content.rstrip("\n"))
            lines.append("")
        lines.append("--- activation (only with --install) ---")
        for command in linux_activation(system_scope, config.duty_keys):
            lines.append(" ".join(command))
        lines.append("")
    return "\n".join(lines)


def write_unit_files(files: list[tuple[Path, str]]) -> None:
    """Write each unit file atomically in the destination directory: a
    fixed-name temporary plus os.replace, so re-installing replaces in
    place and never appends or duplicates."""
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / (path.name + ".new")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)


def install(config: EnrollConfig, target: str) -> None:
    """The only path that writes or activates anything; separate and explicit."""
    if target not in TARGETS:
        raise SystemExit(f"不支持的安装目标: {target}(可选: {', '.join(TARGETS)})")
    if target == "windows":
        if os.name != "nt":
            raise SystemExit("安装目标 windows 需要在 Windows 节点上执行")
        for duty in duties(config):
            subprocess.run(windows_task_argv(config, duty), check=True)
        return
    if os.name != "posix" or shutil.which("systemctl") is None:
        raise SystemExit(f"安装目标 {target} 需要有 systemd 的 Linux 节点")
    system_scope = target == "linux-system"
    if system_scope and os.geteuid() != 0:
        raise SystemExit(
            "安装目标 linux-system 需要 root 权限; 无提权的账号请用 --target linux-user"
        )
    write_unit_files(linux_files(config, linux_unit_dir(system_scope)))
    for command in linux_activation(system_scope, config.duty_keys):
        subprocess.run(command, check=True)


def config_from_values(
    *,
    node: str,
    url: str,
    token_file: str,
    actor_token_file: str,
    runtime: str,
    source: str,
    actor: str,
    privacy: str,
    duty_keys: tuple[str, ...],
    package_path: str = "",
    pins: runtime_pins.RuntimePins | None = None,
) -> EnrollConfig:
    """Build the enrollment configuration.

    The interpreter is the operator's per-node pin when one exists, and is
    otherwise derived at run time from the Python that is running this
    install.  A pinned interpreter that does not exist is refused —
    existence-only, the pinned binary is never executed.
    """
    resolved = pins if pins is not None else runtime_pins.load()
    interpreter = sys.executable
    if resolved.interpreter is not None:
        if not Path(resolved.interpreter).is_file():
            raise SystemExit(
                f"Interpreter pin 指向不存在的文件: {resolved.interpreter}"
                f"(修正或删除 {runtime_pins.pins_file()} 中的 interpreter pin)"
            )
        interpreter = resolved.interpreter
    return EnrollConfig(
        node=node,
        url=url,
        token_file=token_file,
        actor_token_file=actor_token_file,
        runtime=runtime,
        source=source,
        actor=actor,
        privacy=privacy,
        duty_keys=duty_keys,
        interpreter=interpreter,
        package_path=package_path,
    )
