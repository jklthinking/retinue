"""License signing/verification and trial degradation."""

from __future__ import annotations

import datetime as dt
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import license as lic


@pytest.fixture()
def keypair():
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes_raw().hex(),
        private.public_key().public_bytes_raw().hex(),
    )


def payload(days: int = 365) -> dict:
    today = dt.date.today()
    return {
        "customer": "测试教培机构",
        "edition": "pro",
        "seats": 50,
        "issued_at": today.isoformat(),
        "expires_at": (today + dt.timedelta(days=days)).isoformat(),
    }


def test_sign_and_verify(keypair):
    private_hex, public_hex = keypair
    document = lic.sign_license(private_hex, payload())
    verified = lic.verify_license(document, public_hex)
    assert verified["customer"] == "测试教培机构"


def test_tampered_license_rejected(keypair):
    private_hex, public_hex = keypair
    document = lic.sign_license(private_hex, payload())
    document["license"]["seats"] = 5000
    with pytest.raises(ValueError):
        lic.verify_license(document, public_hex)


def test_missing_license_is_trial(tmp_path):
    status = lic.load_license(tmp_path)
    assert status == {
        "present": False,
        "valid": False,
        "trial": True,
        "customer": None,
        "edition": None,
        "seats": None,
        "expires_at": None,
        "error": None,
    }


def test_expired_license_degrades(tmp_path, keypair, monkeypatch):
    private_hex, public_hex = keypair
    monkeypatch.setattr(lic, "PUBLIC_KEY_HEX", public_hex)
    document = lic.sign_license(private_hex, payload(days=-1))
    (tmp_path / lic.LICENSE_FILENAME).write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    status = lic.load_license(tmp_path)
    assert status["present"] and not status["valid"]
    assert status["error"] == "license expired"


def test_valid_license_loads(tmp_path, keypair, monkeypatch):
    private_hex, public_hex = keypair
    monkeypatch.setattr(lic, "PUBLIC_KEY_HEX", public_hex)
    document = lic.sign_license(private_hex, payload())
    (tmp_path / lic.LICENSE_FILENAME).write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    status = lic.load_license(tmp_path)
    assert status["valid"] and not status["trial"]
    assert status["seats"] == 50
