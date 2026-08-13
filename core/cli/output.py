"""Safe text output for consoles and redirected standard streams."""

from __future__ import annotations

import sys
from typing import TextIO


def _use_replacement_fallback(stream: TextIO) -> None:
    """Keep *stream*'s target encoding but make unsupported text printable."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="replace")
    except (OSError, ValueError):
        # Test doubles and embedding hosts may expose a stream that cannot be
        # reconfigured. Their write policy remains under the host's control.
        pass


def configure_output_streams() -> None:
    """Prevent user-visible stdout/stderr text from failing to encode.

    The encoding itself is deliberately preserved. A UTF-8 stream therefore
    keeps the full output, while a legacy console, pipe, or redirected file
    gets only the substitutions its own encoding requires.
    """
    _use_replacement_fallback(sys.stdout)
    _use_replacement_fallback(sys.stderr)
