"""License issuing and verification (Ed25519).

Vendor side (keep the private key OFF customer machines):
    python -m server.license gen-keys --out /path/to/vendor-keys
    python -m server.license sign --key /path/to/vendor-keys/license-signing.key \
        --customer "某教培机构" --edition pro --seats 50 --days 365 \
        --out license.json

Customer side: drop license.json into the server data directory. Absent or
invalid licenses degrade to trial mode (banner only) — the product never
bricks a paying customer over a clock or file problem.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from core.cli.output import configure_output_streams
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# Baked into each release; rotate per major version if ever compromised.
PUBLIC_KEY_HEX = "REPLACE_WITH_VENDOR_PUBLIC_KEY_HEX"

LICENSE_FILENAME = "license.json"


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def sign_license(private_key_hex: str, payload: dict[str, Any]) -> dict[str, Any]:
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    signature = key.sign(_canonical(payload))
    return {"license": payload, "signature": signature.hex()}


def verify_license(document: dict[str, Any], public_key_hex: str | None = None) -> dict[str, Any]:
    """Return the verified payload or raise ValueError."""
    key_hex = public_key_hex or PUBLIC_KEY_HEX
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
    except ValueError as exc:
        raise ValueError("no vendor public key baked into this build") from exc
    payload = document.get("license")
    signature = document.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise ValueError("malformed license document")
    try:
        key.verify(bytes.fromhex(signature), _canonical(payload))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("license signature invalid") from exc
    return payload


def load_license(data_dir: Path | str | None) -> dict[str, Any]:
    """Non-fatal status summary for /api/status and the admin console."""
    status: dict[str, Any] = {
        "present": False,
        "valid": False,
        "trial": True,
        "customer": None,
        "edition": None,
        "seats": None,
        "expires_at": None,
        "error": None,
    }
    if not data_dir:
        return status
    path = Path(data_dir) / LICENSE_FILENAME
    if not path.is_file():
        return status
    status["present"] = True
    try:
        payload = verify_license(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        status["error"] = str(exc)
        return status
    status.update(
        customer=payload.get("customer"),
        edition=payload.get("edition"),
        seats=payload.get("seats"),
        expires_at=payload.get("expires_at"),
    )
    expires = payload.get("expires_at")
    if isinstance(expires, str):
        try:
            expired = dt.date.fromisoformat(expires) < dt.date.today()
        except ValueError:
            status["error"] = "invalid expires_at"
            return status
        if expired:
            status["error"] = "license expired"
            return status
    status["valid"] = True
    status["trial"] = False
    return status


def main(argv: list[str] | None = None) -> int:
    configure_output_streams()
    parser = argparse.ArgumentParser(prog="retinue-license")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("gen-keys")
    gen.add_argument("--out", required=True)

    sign = sub.add_parser("sign")
    sign.add_argument("--key", required=True, help="private key file (hex)")
    sign.add_argument("--customer", required=True)
    sign.add_argument("--edition", default="pro")
    sign.add_argument("--seats", type=int, default=50)
    sign.add_argument("--days", type=int, default=365)
    sign.add_argument("--out", default="license.json")

    verify = sub.add_parser("verify")
    verify.add_argument("path")
    verify.add_argument("--pubkey", help="override baked-in public key (hex)")

    args = parser.parse_args(argv)

    if args.command == "gen-keys":
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        private = Ed25519PrivateKey.generate()
        private_hex = private.private_bytes_raw().hex()
        public_hex = private.public_key().public_bytes_raw().hex()
        key_path = out / "license-signing.key"
        key_path.write_text(private_hex + "\n", encoding="utf-8")
        key_path.chmod(0o600)
        (out / "license-signing.pub").write_text(public_hex + "\n", encoding="utf-8")
        print(f"private: {key_path}\npublic:  {public_hex}")
        return 0

    if args.command == "sign":
        private_hex = Path(args.key).read_text(encoding="utf-8").strip()
        today = dt.date.today()
        payload = {
            "customer": args.customer,
            "edition": args.edition,
            "seats": args.seats,
            "issued_at": today.isoformat(),
            "expires_at": (today + dt.timedelta(days=args.days)).isoformat(),
        }
        document = sign_license(private_hex, payload)
        Path(args.out).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"signed → {args.out}: {payload}")
        return 0

    document = json.loads(Path(args.path).read_text(encoding="utf-8"))
    try:
        payload = verify_license(document, args.pubkey)
    except ValueError as exc:
        print(f"INVALID: {exc}")
        return 1
    print(f"VALID: {payload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
