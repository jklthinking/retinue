"""Compatibility shim: the implementation moved to ``node.http_client``.

The module is aliased in ``sys.modules`` so that attribute access and
``monkeypatch.setattr("server.http_client.<name>", ...)`` keep operating on
the single real implementation.
"""

import sys as _sys

from node import http_client as _impl

_sys.modules[__name__] = _impl
