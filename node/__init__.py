"""Retinue node agent — the managed-node duties without the server stack.

This package holds everything a managed node needs to run: the infrastructure
heartbeat (``probe``), the agent-CLI inventory (``runtime_probe``, with the
data-directory detection in ``data_dirs``), the
privacy-scoped session sync (``push_sessions``), the shared HTTP proxy policy
(``http_client``), the duty scheduler enrollment (``enroll``), and the
``retinue-node`` console entry point (``cli``).

Every module depends only on the standard library plus ``adapters`` (which
reaches ``core.protocol`` for validation constants); nothing here imports the
``server`` package, so a node install never pulls in the web framework, the
ORM, or the ASGI server.  The ``server`` package keeps import-compatible
shims for these modules so existing callers keep working unchanged.
"""
