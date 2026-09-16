import json
from pathlib import Path

import pytest

from core.panel_demo import MARKER, build_panel_demo, route_filename


def test_route_filename_maps_summary_query():
    assert route_filename("/api/summary?today=2026-08-31") == "summary_today_2026-08-31.json"


def test_panel_demo_api_snapshots_are_offline_and_marked(tmp_path):
    dest = tmp_path / "demo"
    build_panel_demo(dest, skip_npm=True)

    assert (dest / MARKER).is_file()
    manifest = json.loads((dest / "build.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "panel-demo"
    assert manifest["network_required"] is False

    index = json.loads((dest / "api" / "_index.json").read_text(encoding="utf-8"))
    assert "/api/auth/me" in index
    assert "/api/summary?today=2026-08-31" in index

    board = (dest / "index.html").read_text(encoding="utf-8")
    assert "#0b1020" not in board
    assert "class='card'" not in board

    for path in dest.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text
        assert "https://" not in text


def test_panel_demo_refuses_unmanaged_output(tmp_path):
    unmanaged = tmp_path / "foreign"
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("keep", encoding="utf-8")
    from core.protocol.task import ProtocolError

    with pytest.raises(ProtocolError, match="non-generated"):
        build_panel_demo(unmanaged)
