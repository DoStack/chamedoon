"""The switch. Read by /public/meta/ on every launch, so a missing table (a Preview never migrates) must only mean off."""

from __future__ import annotations

import logging

from django.db import DatabaseError, transaction

from aisearch.models import SearchSettings

logger = logging.getLogger("aisearch")


def row() -> SearchSettings:
    return SearchSettings.load()


def search_on() -> bool:
    try:
        with transaction.atomic():
            return row().enabled
    except DatabaseError:
        logger.warning("aisearch_settings_unreadable")
        return False
