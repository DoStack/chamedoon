from contextlib import ExitStack
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _outreach_clock_and_model(request):
    """The pre-send kind check calls a model and agent replies wait for the person's local hours. Tests mock both,
    except the tests of those rules themselves (class names with KindCheck or ReplyWindow)."""
    name = request.cls.__name__ if request.cls else ""
    with ExitStack() as stack:
        if "KindCheck" not in name:
            stack.enter_context(patch("outreach.kindcheck.agrees", return_value=True))
            stack.enter_context(patch("outreach.kindcheck.area_ok", return_value=True))
        if "ReplyWindow" not in name:
            stack.enter_context(patch("outreach.agent.in_window", return_value=True))
        yield
