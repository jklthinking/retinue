"""Community-preview export: promotions, quarantine, and scan placement."""

from __future__ import annotations

from pathlib import Path

from scripts.export_community import main, scan_internal_names


INTERNAL = "king" + "dom"
REALM = "王" + "国"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture(root: Path) -> Path:
    source = root / "src"
    _write(source / "LICENSE.md", "FSL body\n")
    _write(source / "LICENSE", "Apache body\n")
    _write(source / "README.md", "internal readme\n")
    _write(source / "README.community.md", "community readme\n")
    _write(source / "SECURITY.md", "internal security\n")
    _write(source / "SECURITY.community.md", "community security\n")
    _write(source / "CONTRIBUTING.md", "internal contributing\n")
    _write(source / "CONTRIBUTING.community.md", "community contributing\n")
    _write(source / "NOTICE", "internal notice\n")
    _write(source / "NOTICE.community", "community notice\n")
    _write(source / "server" / "app.py", "print('ok')\n")
    _write(source / "server" / f"{INTERNAL}.py", "raise SystemExit('internal')\n")
    _write(
        source / "docs" / "examples" / f"{INTERNAL}-context.md",
        "internal theme\n",
    )
    _write(source / "docs" / "design" / "audit-2099.md", "internal audit\n")
    _write(source / "docs" / "evidence" / "note.md", "internal evidence\n")
    _write(source / "scripts" / "install-server.sh", "echo deploy\n")
    _write(source / "scripts" / "backfill_data_governance.py", "print(0)\n")
    _write(
        source / "scripts" / "construction_ledger.yaml",
        "sites:\n"
        "- file: server/app.py\n"
        "  entity: Task\n"
        "  contexts: [create]\n"
        "  decider: operator-command\n"
        "  reason: kept because the file remains in the public tree after export.\n"
        f"- file: server/{INTERNAL}_import.py\n"
        "  entity: Actor\n"
        "  contexts: [apply]\n"
        "  decider: administrator-session\n"
        "  reason: dropped because the target file is removed from the export.\n",
    )
    _write(source / "keep.py", "value = 1\n")
    return source


def test_export_promotes_community_files_and_drops_quarantine(tmp_path: Path):
    source = _fixture(tmp_path)
    dest = tmp_path / "dist" / "community-export"
    report = tmp_path / "dist" / "community-export-scan.txt"

    assert main(["--source", str(source), "--out", str(dest), "--report", str(report)]) == 0

    assert (dest / "README.md").read_text(encoding="utf-8") == "community readme\n"
    assert (dest / "SECURITY.md").read_text(encoding="utf-8") == "community security\n"
    assert (dest / "CONTRIBUTING.md").read_text(encoding="utf-8") == "community contributing\n"
    assert (dest / "NOTICE").read_text(encoding="utf-8") == "community notice\n"
    assert (dest / "LICENSE.md").read_text(encoding="utf-8") == "FSL body\n"
    assert not (dest / "LICENSE").exists()
    assert not (dest / "README.community.md").exists()
    assert not (dest / "server" / f"{INTERNAL}.py").exists()
    assert not (dest / "docs" / "examples" / f"{INTERNAL}-context.md").exists()
    assert not (dest / "docs" / "design" / "audit-2099.md").exists()
    assert not (dest / "docs" / "evidence" / "note.md").exists()
    assert not (dest / "scripts" / "install-server.sh").exists()
    assert not (dest / "scripts" / "backfill_data_governance.py").exists()
    assert (dest / "server" / "app.py").is_file()
    assert (dest / "keep.py").is_file()
    ledger = (dest / "scripts" / "construction_ledger.yaml").read_text(encoding="utf-8")
    assert "server/app.py" in ledger
    assert f"server/{INTERNAL}_import.py" not in ledger
    assert report.is_file()
    assert dest not in report.parents
    assert "result: clean" in report.read_text(encoding="utf-8")
    assert scan_internal_names(dest) == []


def test_export_is_idempotent_and_keeps_the_scan_outside(tmp_path: Path):
    source = _fixture(tmp_path)
    dest = tmp_path / "out"
    report = tmp_path / "scan.txt"
    argv = ["--source", str(source), "--out", str(dest), "--report", str(report)]
    assert main(argv) == 0
    first = (dest / "README.md").read_text(encoding="utf-8")
    assert main(argv) == 0
    assert (dest / "README.md").read_text(encoding="utf-8") == first
    assert report.is_file()
    assert not (dest / report.name).exists()


def test_export_reports_identifier_findings(tmp_path: Path):
    source = _fixture(tmp_path)
    address = "someone" + "@" + "example.invalid"
    _write(source / "leak.md", f"contact {address} please\n")
    dest = tmp_path / "out"
    report = tmp_path / "scan.txt"
    assert main(["--source", str(source), "--out", str(dest), "--report", str(report)]) == 2
    text = report.read_text(encoding="utf-8")
    assert "result: findings" in text
    assert address in text


def test_internal_name_scan_finds_leftover_tokens(tmp_path: Path):
    dest = tmp_path / "tree"
    _write(dest / "note.md", f"mentions {INTERNAL} and {REALM}\n")
    hits = scan_internal_names(dest)
    assert len(hits) == 1
    assert INTERNAL in hits[0]
    assert REALM in hits[0]
