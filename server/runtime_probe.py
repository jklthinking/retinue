"""Compatibility shim: the implementation moved to ``node.runtime_probe``.

The module is aliased in ``sys.modules`` so existing callers and tests keep
working against the single real implementation.  (Decoupling-required edit
only — no logic change; see the node-agent work order.)
"""

import sys as _sys

from node import runtime_probe as _impl

_sys.modules[__name__] = _impl
