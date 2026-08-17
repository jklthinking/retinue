"""Structured acceptance checks evaluated by the server on write-back.

Plain-text acceptance items stay human checklist rows. A JSON object with a
``check`` key is a machine guardrail. Unknown check names fail closed.
"""

from __future__ import annotations

import json
from typing import Any

from core.protocol.task import ProtocolError


KNOWN_CHECKS = ("required_fields", "required_output_field", "tests_green")


def encode_acceptance_item(item: str | dict[str, Any]) -> str:
    """Store a checklist row or a structured check as one acceptance string."""
    if isinstance(item, dict):
        check = str(item.get("check") or "").strip()
        if check not in KNOWN_CHECKS:
            raise ProtocolError(
                f"unknown guardrail check {check!r}; "
                f"known: {', '.join(KNOWN_CHECKS)}"
            )
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    text = str(item or "").strip()
    if not text:
        return ""
    parsed = parse_guardrail(text)
    if parsed is not None and parsed.get("check") not in KNOWN_CHECKS:
        raise ProtocolError(
            f"unknown guardrail check {parsed.get('check')!r}; "
            f"known: {', '.join(KNOWN_CHECKS)}"
        )
    return text


def encode_acceptance(items: list[Any] | None) -> list[str]:
    encoded = [encode_acceptance_item(item) for item in (items or [])]
    return [item for item in encoded if item]


def parse_guardrail(item: str) -> dict[str, Any] | None:
    text = (item or "").strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    check = str(payload.get("check") or "").strip()
    if not check:
        return None
    return payload


def structured_checks(acceptance: list[str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in acceptance:
        parsed = parse_guardrail(item)
        if parsed is not None:
            checks.append(parsed)
    return checks


def _field_map(evidence: dict[str, Any] | None) -> dict[str, Any]:
    payload = evidence or {}
    merged: dict[str, Any] = {}
    output = payload.get("output")
    fields = payload.get("fields")
    if isinstance(output, dict):
        merged.update(output)
    if isinstance(fields, dict):
        merged.update(fields)
    return merged


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _eval_required_fields(
    check: dict[str, Any], evidence: dict[str, Any] | None
) -> str | None:
    names = check.get("fields")
    if names is None and check.get("field"):
        names = [check.get("field")]
    if not isinstance(names, list) or not names:
        return "required_fields needs a non-empty fields list"
    found = _field_map(evidence)
    missing = [
        str(name)
        for name in names
        if not str(name).strip() or not _is_present(found.get(str(name)))
    ]
    if missing:
        return "missing required field(s): " + ", ".join(missing)
    return None


def _eval_tests_green(
    check: dict[str, Any], evidence: dict[str, Any] | None
) -> str | None:
    del check
    payload = evidence or {}
    tests = payload.get("tests")
    if not isinstance(tests, dict) or not tests:
        return "tests result missing"
    status = str(tests.get("status") or "").strip().lower()
    if status in {"green", "passed", "pass", "ok"}:
        failed = tests.get("failed")
        if failed is not None and int(failed) != 0:
            return f"tests are not green: failed={failed}"
        return None
    if "failed" not in tests and "passed" not in tests:
        return "tests result missing passed/failed counts"
    try:
        failed = int(tests.get("failed") or 0)
        passed = int(tests.get("passed") or 0)
    except (TypeError, ValueError):
        return "tests counts must be integers"
    if failed != 0:
        return f"tests are not green: failed={failed}"
    if passed < 0:
        return "tests passed count is invalid"
    return None


_EVALUATORS = {
    "required_fields": _eval_required_fields,
    "required_output_field": _eval_required_fields,
    "tests_green": _eval_tests_green,
}


def evaluate_guardrails(
    acceptance: list[str],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a machine verdict. No structured checks means a pass."""
    checks = structured_checks(acceptance)
    results: list[dict[str, Any]] = []
    reasons: list[str] = []
    for check in checks:
        name = str(check.get("check") or "").strip()
        evaluator = _EVALUATORS.get(name)
        if evaluator is None:
            reason = f"unknown guardrail check {name!r}"
            results.append({"check": name or "unknown", "ok": False, "reason": reason})
            reasons.append(reason)
            continue
        reason = evaluator(check, evidence)
        ok = reason is None
        row = {"check": name, "ok": ok}
        if reason:
            row["reason"] = reason
            reasons.append(reason)
        results.append(row)
    return {
        "passed": not reasons,
        "checks": results,
        "reasons": reasons,
    }


def assert_done_allowed(
    acceptance: list[str],
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refuse a done transition when a structured check does not pass."""
    verdict = evaluate_guardrails(acceptance, evidence)
    if not verdict["passed"]:
        raise ProtocolError(
            "guardrail rejected done: " + "; ".join(verdict["reasons"])
        )
    return verdict
