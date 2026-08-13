import json
from pathlib import Path

import pytest

from core.protocol.task import ProtocolError
from core.static_demo import MARKER, build_static_demo


def snapshot(root: Path):
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_static_demo_is_seed_42_offline_and_reproducible(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    pages = build_static_demo(first)
    build_static_demo(second)

    assert snapshot(first) == snapshot(second)
    manifest = json.loads((first / "build.json").read_text())
    assert manifest["seed"] == 42
    assert manifest["network_required"] is False
    assert len(pages) == 9
    assert (first / MARKER).is_file()

    board = (first / "index.html").read_text()
    overview = (first / "overview.html").read_text()
    assert board.count("class='card'") == 6
    assert "href='overview.html'" in board
    assert "href='index.html'" in overview
    assert "sample-seed-42" in overview
    for content in snapshot(first).values():
        if content.startswith(b"<!doctype html>"):
            assert b"http://" not in content
            assert b"https://" not in content
            assert b"href='/'" not in content


def test_static_demo_rebuilds_marked_output_but_protects_other_directories(tmp_path):
    generated = tmp_path / "generated"
    build_static_demo(generated)
    (generated / "stale.html").write_text("stale", encoding="utf-8")
    build_static_demo(generated)
    assert not (generated / "stale.html").exists()

    unmanaged = tmp_path / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ProtocolError, match="non-generated"):
        build_static_demo(unmanaged)
    assert (unmanaged / "keep.txt").read_text() == "keep"
