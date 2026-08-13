import itertools

import pytest

from core.protocol.task import ProtocolError, STATES, TRANSITIONS, validate_transition


LEGAL = {(old, new) for old, targets in TRANSITIONS.items() for new in targets}


@pytest.mark.parametrize("old,new", sorted(LEGAL))
def test_all_legal_transitions(old, new):
    validate_transition(old, new)


@pytest.mark.parametrize(
    "old,new",
    sorted(
        (old, new)
        for old, new in itertools.product(STATES, repeat=2)
        if old != new and (old, new) not in LEGAL
    ),
)
def test_all_illegal_transitions(old, new):
    """Security negative cases: docs/security.md#sec-3-append-only-chain-and-legal-transitions."""
    with pytest.raises(ProtocolError, match="illegal status transition"):
        validate_transition(old, new)


def test_unknown_state_is_rejected():
    """Security negative case: docs/security.md#sec-3-append-only-chain-and-legal-transitions."""
    with pytest.raises(ProtocolError, match="unknown status"):
        validate_transition("queued", "unknown")
