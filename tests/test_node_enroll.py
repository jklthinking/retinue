"""Enrollment: rendered output for the three targets, refusal, idempotence.

Every assertion here runs against synthetic configuration and the pure
render/plan functions, or against install with the platform and every
side-effecting call monkeypatched away.  No test writes a real unit, creates
a real scheduled task, or activates anything on the host — a test that
installs a real unit is a failed test.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

import node.cli
from node import enroll

URL = "http://127.0.0.1:9219"

CONFIG = enroll.EnrollConfig(
    node="synthetic-node",
    url=URL,
    token_file="node-token",
    actor_token_file="actor-token",
    runtime="codex",
    source="sessions",
    actor="agent-1",
    privacy="metadata",
    duty_keys=("heartbeat", "runtimes", "sessions"),
    interpreter="python3",
)

_HEARTBEAT_SERVICE = """\
[Unit]
Description=Retinue node infrastructure heartbeat (managed node duty)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
Environment=RETINUE_SERVER_URL=http://127.0.0.1:9219
Environment=RETINUE_NODE_ID=synthetic-node
Environment=RETINUE_NODE_TOKEN_FILE=node-token
ExecStart=python3 -m node.cli heartbeat
"""

_RUNTIMES_SERVICE = """\
[Unit]
Description=Retinue node agent CLI inventory (managed node duty)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
Environment=RETINUE_SERVER_URL=http://127.0.0.1:9219
Environment=RETINUE_NODE_ID=synthetic-node
Environment=RETINUE_NODE_TOKEN_FILE=node-token
ExecStart=python3 -m node.cli runtimes
"""

_SESSIONS_SERVICE = """\
[Unit]
Description=Retinue node privacy-scoped session index (managed node duty)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
Environment=RETINUE_SERVER_URL=http://127.0.0.1:9219
Environment=RETINUE_NODE_ID=synthetic-node
Environment=RETINUE_ACTOR_TOKEN_FILE=actor-token
ExecStart=python3 -m node.cli sync-sessions --runtime codex --source sessions --actor agent-1 --privacy metadata
"""


def _timer(description: str, cadence: str, jitter: str) -> str:
    return f"""\
[Unit]
Description=Run the Retinue node {description} {cadence}

[Timer]
OnCalendar={cadence}
RandomizedDelaySec={jitter}
Persistent=true

[Install]
WantedBy=timers.target
"""


# The jitter values are spelled out here on purpose. Importing them from the
# module under test would make this golden comparison agree with any future
# change automatically, which is the one thing a golden test must not do.
_HEARTBEAT_TIMER = _timer("infrastructure heartbeat", "hourly", "5min")
_RUNTIMES_TIMER = _timer("agent CLI inventory", "hourly", "5min")
_SESSIONS_TIMER = _timer("privacy-scoped session index", "daily", "30min")


def _expected_linux(target: str, unit_dir: str, systemctl: str) -> str:
    units = [
        ("heartbeat.service", _HEARTBEAT_SERVICE),
        ("heartbeat.timer", _HEARTBEAT_TIMER),
        ("runtimes.service", _RUNTIMES_SERVICE),
        ("runtimes.timer", _RUNTIMES_TIMER),
        ("sessions.service", _SESSIONS_SERVICE),
        ("sessions.timer", _SESSIONS_TIMER),
    ]
    parts = [
        f"retinue-node enroll --target {target}"
        " — render only; nothing written or activated",
        "",
    ]
    for suffix, content in units:
        parts.append(f"--- {unit_dir}/retinue-node-{suffix} ---")
        parts.append(content.rstrip("\n"))
        parts.append("")
    parts.append("--- activation (only with --install) ---")
    parts.append(f"{systemctl} daemon-reload")
    parts.append(
        f"{systemctl} enable --now retinue-node-heartbeat.timer"
        " retinue-node-runtimes.timer retinue-node-sessions.timer"
    )
    parts.append("")
    return "\n".join(parts)


EXPECTED_WINDOWS = """\
retinue-node enroll --target windows — render only; nothing written or activated

--- scheduled task: Retinue Node Heartbeat (hourly) ---
schtasks /Create /TN 'Retinue Node Heartbeat' /SC HOURLY /TR 'python3 -m node.cli heartbeat --node synthetic-node --url http://127.0.0.1:9219 --token-file node-token' /F

--- scheduled task: Retinue Node Runtimes (hourly) ---
schtasks /Create /TN 'Retinue Node Runtimes' /SC HOURLY /TR 'python3 -m node.cli runtimes --node synthetic-node --url http://127.0.0.1:9219 --token-file node-token' /F

--- scheduled task: Retinue Node Sessions (daily) ---
schtasks /Create /TN 'Retinue Node Sessions' /SC DAILY /TR 'python3 -m node.cli sync-sessions --runtime codex --source sessions --actor agent-1 --privacy metadata --node synthetic-node --url http://127.0.0.1:9219 --actor-token-file actor-token' /F
"""


def test_render_linux_system_matches_expected():
    assert enroll.render(CONFIG, "linux-system") == _expected_linux(
        "linux-system", "/etc/systemd/system", "systemctl"
    )


def test_render_linux_user_matches_expected():
    unit_dir = str(Path.home() / ".config/systemd/user")
    assert enroll.render(CONFIG, "linux-user") == _expected_linux(
        "linux-user", unit_dir, "systemctl --user"
    )


def test_render_windows_matches_expected():
    assert enroll.render(CONFIG, "windows") == EXPECTED_WINDOWS


def test_cadence_hourly_hourly_daily():
    cadences = {duty.key: duty.cadence for duty in enroll.duties(CONFIG)}
    assert cadences == {"heartbeat": "hourly", "runtimes": "hourly", "sessions": "daily"}


def test_render_twice_is_byte_identical():
    for target in enroll.TARGETS:
        assert enroll.render(CONFIG, target) == enroll.render(CONFIG, target)


def test_write_unit_files_replaces_in_place(tmp_path):
    files = enroll.linux_files(CONFIG, tmp_path)
    enroll.write_unit_files(files)
    enroll.write_unit_files(files)
    written = sorted(path.name for path in tmp_path.iterdir())
    assert written == sorted(path.name for path, _ in files)
    for path, content in files:
        assert path.read_text(encoding="utf-8") == content


def test_windows_tasks_replace_rather_than_append():
    for duty in enroll.duties(CONFIG):
        argv = enroll.windows_task_argv(CONFIG, duty)
        assert argv[0] == "schtasks"
        assert "/Create" in argv
        assert argv[-1] == "/F"  # force-overwrite the fixed-name task
        assert argv[argv.index("/SC") + 1] == duty.cadence.upper()


@pytest.fixture()
def token_file(tmp_path):
    path = tmp_path / "node-token"
    path.write_text("synthetic-token\n", encoding="utf-8")
    return path


@pytest.fixture()
def actor_token_file(tmp_path):
    path = tmp_path / "actor-token"
    path.write_text("synthetic-actor-token\n", encoding="utf-8")
    return path


@pytest.fixture()
def clear_env(monkeypatch):
    for name in (
        "RETINUE_SERVER_URL",
        "RETINUE_NODE_ID",
        "RETINUE_NODE_TOKEN_FILE",
        "RETINUE_ACTOR_TOKEN_FILE",
        "RETINUE_PACKAGE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def _enroll_argv(token_file, actor_token_file=None, *extra):
    argv = [
        "enroll", "--target", "linux-system",
        "--node", "synthetic-node", "--url", URL,
        "--token-file", str(token_file),
        "--runtime", "codex", "--source", "sessions", "--actor", "agent-1",
    ]
    if actor_token_file is not None:
        argv += ["--actor-token-file", str(actor_token_file)]
    return [*argv, *extra]


def test_cli_default_renders_and_touches_nothing(
    monkeypatch, token_file, actor_token_file, clear_env, capsys
):
    def forbidden(*args, **kwargs):
        raise AssertionError("render mode must not write or activate anything")

    monkeypatch.setattr(enroll, "install", forbidden)
    monkeypatch.setattr(enroll, "write_unit_files", forbidden)
    monkeypatch.setattr(enroll.subprocess, "run", forbidden)

    assert node.cli.main(_enroll_argv(token_file, actor_token_file)) == 0
    output = capsys.readouterr().out
    assert "render only; nothing written or activated" in output
    assert "[Install]" in output
    # Nothing was created next to the token files.
    assert sorted(path.name for path in token_file.parent.iterdir()) == [
        "actor-token",
        "node-token",
    ]


def test_cli_install_is_separate_and_explicit(
    monkeypatch, token_file, actor_token_file, clear_env, capsys
):
    calls = []
    monkeypatch.setattr(enroll, "install", lambda config, target: calls.append((config, target)))

    assert node.cli.main(_enroll_argv(token_file, actor_token_file, "--install")) == 0
    assert len(calls) == 1
    config, target = calls[0]
    assert target == "linux-system"
    assert config.node == "synthetic-node"
    assert config.interpreter == sys.executable  # derived at run time
    assert "节点调度已安装" in capsys.readouterr().out


def test_missing_node_id_refuses(token_file, actor_token_file, clear_env):
    argv = _enroll_argv(token_file, actor_token_file)
    argv.remove("--node")
    argv.remove("synthetic-node")
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(argv)
    assert "--node" in str(excinfo.value)


def test_missing_token_file_refuses(clear_env):
    argv = [
        "enroll", "--target", "linux-system", "--node", "synthetic-node",
        "--runtime", "codex", "--source", "sessions", "--actor", "agent-1",
    ]
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(argv)
    assert "--token-file" in str(excinfo.value)


def test_unreadable_token_file_refuses(tmp_path, actor_token_file, clear_env):
    missing = tmp_path / "no-such-token"
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(_enroll_argv(missing, actor_token_file))
    assert "不可读" in str(excinfo.value)
    assert str(missing) in str(excinfo.value)


def test_empty_token_file_refuses(tmp_path, actor_token_file, clear_env):
    empty = tmp_path / "node-token"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(_enroll_argv(empty, actor_token_file))
    assert "为空" in str(excinfo.value)


def test_missing_session_argument_refuses(token_file, actor_token_file, clear_env):
    argv = _enroll_argv(token_file, actor_token_file)
    argv.remove("--runtime")
    argv.remove("codex")
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(argv)
    assert excinfo.value.code != 0
    assert "--runtime" in str(excinfo.value)


def test_unsupported_target_refuses(token_file, actor_token_file, clear_env, capsys):
    argv = _enroll_argv(token_file, actor_token_file)
    argv[argv.index("linux-system")] = "qubes"
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(argv)
    assert excinfo.value.code == 2
    assert "qubes" in capsys.readouterr().err


def test_install_refuses_wrong_platform_without_side_effects(monkeypatch):
    calls = []
    monkeypatch.setattr(enroll.subprocess, "run", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(enroll.os, "name", "posix")
    with pytest.raises(SystemExit) as excinfo:
        enroll.install(CONFIG, "windows")
    assert "Windows" in str(excinfo.value)
    assert calls == []


def test_install_refuses_unprivileged_system_scope(monkeypatch):
    writes = []
    calls = []
    monkeypatch.setattr(enroll.os, "name", "posix")
    monkeypatch.setattr(enroll.shutil, "which", lambda command: command)
    monkeypatch.setattr(enroll.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(enroll, "write_unit_files", lambda files: writes.append(files))
    monkeypatch.setattr(enroll.subprocess, "run", lambda *a, **kw: calls.append(a))
    with pytest.raises(SystemExit) as excinfo:
        enroll.install(CONFIG, "linux-system")
    assert "linux-user" in str(excinfo.value)
    assert writes == []
    assert calls == []


def test_sessions_duty_without_actor_credential_refuses_with_no_side_effects(
    monkeypatch, token_file, clear_env
):
    """Asking for the session duty without its actor credential must fail
    before anything is written — even in --install mode — and must name
    exactly what is missing."""
    calls = []
    monkeypatch.setattr(enroll, "install", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(enroll, "write_unit_files", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(
        enroll.subprocess, "run", lambda *a, **kw: calls.append(a)
    )
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(_enroll_argv(token_file, None, "--install"))
    assert excinfo.value.code != 0
    message = str(excinfo.value)
    assert "--actor-token-file" in message
    assert "RETINUE_ACTOR_TOKEN_FILE" in message
    # Absence of side effects: no install/write ran, and the only file in
    # the directory is the token file the test itself created.
    assert calls == []
    assert [path.name for path in token_file.parent.iterdir()] == ["node-token"]


def test_unreadable_actor_token_file_refuses(token_file, tmp_path, clear_env):
    missing = tmp_path / "no-such-actor-token"
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(_enroll_argv(token_file, missing))
    assert "不可读" in str(excinfo.value)
    assert str(missing) in str(excinfo.value)


def test_empty_actor_token_file_refuses(token_file, tmp_path, clear_env):
    empty = tmp_path / "actor-token"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(_enroll_argv(token_file, empty))
    assert "为空" in str(excinfo.value)


def test_node_duties_only_succeed_with_node_credential_alone(
    token_file, clear_env, capsys
):
    """--duties heartbeat,runtimes needs exactly one secret: the node token.
    No actor token file, no session configuration."""
    argv = [
        "enroll", "--target", "linux-system",
        "--node", "synthetic-node", "--url", URL,
        "--token-file", str(token_file),
        "--duties", "heartbeat,runtimes",
    ]
    assert node.cli.main(argv) == 0
    output = capsys.readouterr().out
    assert "retinue-node-heartbeat.service" in output
    assert "retinue-node-runtimes.service" in output
    assert "sessions" not in output
    assert "RETINUE_ACTOR_TOKEN_FILE" not in output
    assert "RETINUE_NODE_TOKEN_FILE" in output


def test_unknown_duty_refuses(token_file, clear_env):
    argv = [
        "enroll", "--target", "linux-system",
        "--node", "synthetic-node", "--url", URL,
        "--token-file", str(token_file),
        "--duties", "heartbeat,defrag",
    ]
    with pytest.raises(SystemExit) as excinfo:
        node.cli.main(argv)
    assert "defrag" in str(excinfo.value)


def test_each_duty_carries_its_own_credential_kind():
    """The rendered session unit authenticates with the actor credential;
    the node units carry the heartbeat-only node credential."""
    units = {
        path.name: content
        for path, content in enroll.linux_files(CONFIG, Path("units"))
    }
    sessions = units["retinue-node-sessions.service"]
    assert "Environment=RETINUE_ACTOR_TOKEN_FILE=actor-token" in sessions
    assert "RETINUE_NODE_TOKEN_FILE" not in sessions
    for name in ("retinue-node-heartbeat.service", "retinue-node-runtimes.service"):
        assert "Environment=RETINUE_NODE_TOKEN_FILE=node-token" in units[name]
        assert "RETINUE_ACTOR_TOKEN_FILE" not in units[name]
    sessions_duty = next(d for d in enroll.duties(CONFIG) if d.key == "sessions")
    argv = enroll.windows_task_argv(CONFIG, sessions_duty)
    run = argv[argv.index("/TR") + 1]
    assert "--actor-token-file actor-token" in run
    assert "--token-file" not in run


# A path-based deployment: the package is copied onto the node's disk and
# found through PYTHONPATH, because the node cannot install it (no pip, no
# venv, a managed interpreter). The path below is a synthetic placeholder,
# not a machine path.
PATH_CONFIG = dataclasses.replace(CONFIG, package_path="/opt/retinue/package")

_PATH_HEARTBEAT_SERVICE = """\
[Unit]
Description=Retinue node infrastructure heartbeat (managed node duty)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
Environment=RETINUE_SERVER_URL=http://127.0.0.1:9219
Environment=RETINUE_NODE_ID=synthetic-node
Environment=PYTHONPATH=/opt/retinue/package
Environment=RETINUE_NODE_TOKEN_FILE=node-token
ExecStart=python3 -m node.cli heartbeat
"""


def test_path_based_deployment_carries_the_import_path_byte_exactly():
    units = {
        path.name: content
        for path, content in enroll.linux_files(PATH_CONFIG, Path("units"))
    }
    assert units["retinue-node-heartbeat.service"] == _PATH_HEARTBEAT_SERVICE
    for name, content in units.items():
        if name.endswith(".service"):
            assert "Environment=PYTHONPATH=/opt/retinue/package\n" in content
        else:
            # Timers carry no command line; they stay byte-identical.
            assert "PYTHONPATH" not in content


def test_path_based_windows_task_carries_the_import_path_byte_exactly():
    for duty in enroll.duties(PATH_CONFIG):
        argv = enroll.windows_task_argv(PATH_CONFIG, duty)
        run = argv[argv.index("/TR") + 1]
        assert run.startswith(
            'cmd /c "set PYTHONPATH=/opt/retinue/package && python3 -m node.cli '
        )
        assert run.endswith('"')
    heartbeat = next(d for d in enroll.duties(PATH_CONFIG) if d.key == "heartbeat")
    argv = enroll.windows_task_argv(PATH_CONFIG, heartbeat)
    assert argv[argv.index("/TR") + 1] == (
        'cmd /c "set PYTHONPATH=/opt/retinue/package && python3 -m node.cli '
        "heartbeat --node synthetic-node --url http://127.0.0.1:9219 "
        '--token-file node-token"'
    )


def test_without_package_path_nothing_mentions_pythonpath():
    for target in enroll.TARGETS:
        assert "PYTHONPATH" not in enroll.render(CONFIG, target)
    assert "PYTHONPATH" not in enroll.render(PATH_CONFIG, "linux-system").replace(
        "PYTHONPATH=/opt/retinue/package", ""
    )


def test_cli_package_path_argument_and_environment(
    monkeypatch, token_file, clear_env, capsys
):
    argv = [
        "enroll", "--target", "linux-system",
        "--node", "synthetic-node", "--url", URL,
        "--token-file", str(token_file),
        "--duties", "heartbeat,runtimes",
        "--package-path", "/opt/retinue/package",
    ]
    assert node.cli.main(argv) == 0
    assert "Environment=PYTHONPATH=/opt/retinue/package" in capsys.readouterr().out

    monkeypatch.setenv("RETINUE_PACKAGE_PATH", "/opt/retinue/from-env")
    argv.remove("--package-path")
    argv.remove("/opt/retinue/package")
    assert node.cli.main(argv) == 0
    assert "Environment=PYTHONPATH=/opt/retinue/from-env" in capsys.readouterr().out
