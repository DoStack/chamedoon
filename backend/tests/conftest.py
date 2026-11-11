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
            stack.enter_context(patch("outreach.kindcheck.clear", return_value=True))
            stack.enter_context(patch("outreach.kindcheck.not_housing", return_value=False))
        if "Writer" not in name:
            stack.enter_context(patch("outreach.writer.rewrite", side_effect=lambda draft, **_kwargs: draft))
        if "ReplyWindow" not in name:
            stack.enter_context(patch("outreach.agent.in_window", return_value=True))
        yield


# Modules that test the template pages (unrouted in production since 2026-11-11) route them again for their tests.
LEGACY_PAGE_MODULES = {
    "test_contact_gate", "test_courier_entry", "test_courier_flow", "test_courier_intro_everywhere", "test_golden_paths", "test_home_alerts",
    "test_home_api", "test_home_freshness", "test_home_gallery", "test_home_miniapp", "test_home_post",
    "test_home_post_buttons", "test_home_search", "test_miniapp", "test_outreach", "test_safety", "test_server_fixes",
    "test_spa_entry", "test_support",
}


@pytest.fixture(autouse=True)
def _legacy_page_routes(request, settings):
    if request.module.__name__.rsplit(".", 1)[-1] in LEGACY_PAGE_MODULES:
        settings.ROOT_URLCONF = "config.legacy_urls"
    yield
