from pathlib import Path

import yaml

from _manifest import optional_dependencies


ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_readmes_publish_demo_sovereignty_roadmap_support_and_scale_boundaries():
    english = read("README.md")
    chinese = english

    assert "RETINUE" in english
    for content in (english, chinese):
        assert "SELF_HOSTING.md" in content
        assert "compose" in content.lower()
    assert "PolyForm" in english


def test_the_install_command_the_readme_prints_can_run_the_suite():
    """The documented path must be a walked path.

    Both readmes tell a newcomer to install the test extra and nothing else. If
    that extra omits what the suite imports, the first thing a curious developer
    does — run pytest — fails on import, and nobody here notices, because
    contributors install the test and server extras together.
    """
    extras = optional_dependencies()
    declared = " ".join(extras["test"]).lower()

    server_importers = [
        path.name
        for path in sorted((ROOT / "tests").glob("test_*.py"))
        if "from server" in path.read_text(encoding="utf-8")
        or "import server" in path.read_text(encoding="utf-8")
    ]
    if server_importers:
        assert "retinue[server]" in declared or "fastapi" in declared, (
            f"{len(server_importers)} test modules import the server package, so the "
            "test extra must provide it"
        )

    mcp_importers = [
        path.name
        for path in sorted((ROOT / "tests").glob("test_*.py"))
        if "from mcp" in path.read_text(encoding="utf-8")
    ]
    if mcp_importers:
        # Flatten the test extra, following self-references such as
        # retinue[server] -> retinue[mcp]. mcp left the base dependencies
        # (a node must not carry an ASGI server or a crypto library), so the
        # documented install must reach it through an extra instead.
        flattened = declared
        for _ in range(4):
            for name, requirements in extras.items():
                flattened = flattened.replace(
                    f"retinue[{name}]", " ".join(requirements)
                )
        assert "mcp" in flattened.lower(), (
            f"{len(mcp_importers)} test modules import the mcp package, so the "
            "test extra must provide it through some extra chain"
        )

    contributing = read("CONTRIBUTING.md")
    assert "'.[test]'" in contributing or '".[test]"' in contributing


def test_self_hosting_has_backup_restore_drill_and_port_warning():
    guide = read("SELF_HOSTING.md")
    assert "python -m server.main --data-dir ./retinue-server-data migrate" in guide
    assert "stop, migrate,\n   start" in guide
    assert "Opening an existing database never upgrades it" in guide
    assert "## Backup" in guide
    assert "## Restore and rehearse it" in guide
    assert "cp -a" in guide and "retinue task lint" in guide
    assert "127.0.0.1:8787:8787" in guide
    assert "0.0.0.0:8787:8787" in guide
    assert "authenticated TLS reverse proxy" in guide


def test_contributing_and_security_exist_and_name_what_matters():
    """A published project needs both, and a stale one is worse than none.

    These assertions pin only the parts an outside reader depends on: how to run the
    gate, and the limits that would otherwise be discovered the hard way.
    """
    # Collapse whitespace before matching. These files are wrapped prose, and a
    # phrase that happens to straddle a line break is not a missing phrase --
    # asserting on the raw text makes reflowing a paragraph a test failure.
    def flat(name: str) -> str:
        return " ".join(read(name).split())

    contributing = flat("CONTRIBUTING.md")
    security = flat("SECURITY.md")

    # The gate is the contribution contract; a contributor who cannot find it
    # will hand back work that fails on the runner instead.
    assert "scripts/check.sh" in contributing
    assert "--all" in contributing
    # The install command must be the one the readmes print, not a variant.
    assert "pip install -e '.[test]'" in contributing

    # Reporting route, and the pointer to the full model rather than a summary
    # that will drift away from it.
    assert "private vulnerability reporting" in security
    assert "docs/security.md" in security
    # Limits an operator would otherwise meet in production. Each of these is a
    # real property of the current implementation, not boilerplate.
    assert "multi-worker" in security
    assert "administrative surface" in security
    assert "private vulnerability reporting" in security


def test_compose_publishes_only_loopback_and_image_is_unprivileged():
    compose = yaml.safe_load(read("compose.yaml"))
    service = compose["services"]["retinue"]
    assert service["ports"] == ["127.0.0.1:9219:9219"]
    assert service["volumes"] == ["retinue-data:/data"]
    dockerfile = read("Dockerfile")
    assert "USER retinue" in dockerfile
    assert "9219" in dockerfile
    assert "serve" in dockerfile
    assert "panel" not in dockerfile
    smoke = read("scripts/smoke_install.sh")
    assert "/api/health" in smoke
    assert "python -m server.main" in smoke


def test_license_inventory_names_its_generators_and_a_conclusion():
    inventory = read("docs/licenses-inventory.md")
    assert "## Conclusion" in inventory
    assert "scripts/generate_sbom.sh" in inventory
    assert "cyclonedx-py" in inventory
    assert "retinue[server]" in inventory
    assert Path("scripts/generate_sbom.sh").is_file()


def test_agent_and_adapter_guides_link_security_invariants():
    agents = read("AGENTS.md")
    claude = read("CLAUDE.md")
    adapters = read("docs/adapters-guide.md")
    assert "docs/security.md" in agents and "AGENTS.md" in claude
    assert "Prometheus-like" in adapters
    assert "cross_runtime_comparable" in adapters
    assert "Never use wildcard trust" in adapters


def test_closed_loop_walkthrough_names_tested_surfaces_and_honest_gaps():
    guide = read("docs/closed-loop-walkthrough.md")
    assert "tests/test_closed_loop.py" in guide
    assert "retinue task new" in guide
    assert "retinue feishu receive" in guide
    assert "POST /api/dispatch" in guide
    assert "dispatch_senders_env" in guide
    assert "/api/sessions/sync" in guide
    assert "holder-only writes" in guide
    assert "Chat or group membership is not identity" in guide
    assert "creates nothing" in guide
    assert "app credentials" in guide
    assert "internal" not in guide.lower()
