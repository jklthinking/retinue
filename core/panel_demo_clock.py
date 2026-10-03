"""A fixed clock for the standalone offline builder, never the live server."""

from __future__ import annotations

import datetime as dt
import sys
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch


@contextmanager
def demo_clock(day: str):
    """Freeze only Retinue module bindings; do not mutate stdlib datetime.

    This builder runs alone, before it starts a TestClient. Restoring every
    binding also keeps subsequent live-server tests on the real clock.
    """
    anchor = dt.datetime.fromisoformat(f"{day}T09:42:00+00:00")
    real_date, real_datetime = dt.date, dt.datetime

    class DateMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, real_date)

    class DateTimeMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, real_datetime)

    class DemoDate(real_date, metaclass=DateMeta):
        @classmethod
        def today(cls):
            return anchor.date()

    class DemoDateTime(real_datetime, metaclass=DateTimeMeta):
        @classmethod
        def now(cls, tz=None):
            return anchor.astimezone(tz) if tz else anchor.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return anchor.replace(tzinfo=None)

    frozen = SimpleNamespace(**{**vars(dt), "date": DemoDate, "datetime": DemoDateTime})
    with ExitStack() as stack:
        for name, module in list(sys.modules.items()):
            if module is None or name == __name__ or not name.startswith(("core.", "server.")):
                continue
            for attr, value in list(vars(module).items()):
                replacement = frozen if value is dt else DemoDateTime if value is real_datetime else DemoDate if value is real_date else None
                if replacement is not None:
                    stack.enter_context(patch.object(module, attr, replacement))
        def advance(seconds: int):
            nonlocal anchor
            if seconds < 0:
                raise ValueError("demo clock must move forward")
            anchor += dt.timedelta(seconds=seconds)

        yield advance
