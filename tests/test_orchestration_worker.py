"""Worker helpers classify failures and stop on a fenced lease."""

from __future__ import annotations

from tools.retinue_worker import classify_failure, lease_term_of


def test_classify_failure_splits_transient_and_semantic():
    assert classify_failure("connection reset while waiting") == "transient"
    assert classify_failure("queue timed out") == "transient"
    assert classify_failure("quota exhausted") == "semantic"
    assert classify_failure("context overflow on the prompt") == "semantic"


def test_lease_term_of_reads_claim_payload():
    assert lease_term_of({"lease": {"term": 3}}) == 3
    assert lease_term_of({}) == 0
    assert lease_term_of({"lease": {"term": "x"}}) == 0
