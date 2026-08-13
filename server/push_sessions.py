"""Compatibility shim: the implementation moved to ``node.push_sessions``.

The module is aliased in ``sys.modules`` so existing callers and tests keep
working against the single real implementation.
"""

import sys as _sys

from node import push_sessions as _impl

_sys.modules[__name__] = _impl
