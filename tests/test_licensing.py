"""Guard against split license metadata and loss of notices in releases."""
import hashlib
import json
from pathlib import Path
from _manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_AGPL_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"


def test_official_agpl_text_metadata_and_release_inputs_agree():
    assert hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest() == OFFICIAL_AGPL_SHA256
    manifest = load_manifest()
    assert manifest["project"]["license"]["text"] == "AGPL-3.0-only"
    assert "License :: OSI Approved :: GNU Affero General Public License v3" in manifest["project"]["classifiers"]
    for name in ("LICENSE", "NOTICE", "COMMERCIAL.md", "TRADEMARKS.md"):
        assert name in manifest["tool"]["setuptools"]["license-files"]
        assert "include " + name in (ROOT / "MANIFEST.in").read_text()
        assert name in (ROOT / "Dockerfile").read_text()
    assert (ROOT / "NOTICE").read_bytes() == (ROOT / "NOTICE.community").read_bytes()
    assert "Copyright (c) 2026 JKL Thinking" in (ROOT / "NOTICE").read_text()
    assert json.loads((ROOT / "webui/package.json").read_text())["license"] == "AGPL-3.0-only"
    assert json.loads((ROOT / "webui/package-lock.json").read_text())["packages"][""]["license"] == "AGPL-3.0-only"
