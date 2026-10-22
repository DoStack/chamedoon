"""Operator actions on the outreach queue (admin). Each returns how many messages changed.

A message that was sent or is being sent is never touched. Approving only puts a message back
in the queue: the claim still re-checks opt-outs, cooldown, matches and age before sending.
"""

from __future__ import annotations

from django.utils import timezone

from outreach.models import OutreachStatus


def approve_messages(queryset) -> int:
    # Never undo an opt-out from here: remove the opt-out row first if that is really wanted.
    waiting = queryset.filter(status__in=(OutreachStatus.HELD, OutreachStatus.SKIPPED)).exclude(error_code="opted_out")
    return waiting.update(
        status=OutreachStatus.QUEUED, error_code="", error_detail="", updated_at=timezone.now()
    )


def skip_messages(queryset) -> int:
    return queryset.filter(status__in=(OutreachStatus.QUEUED, OutreachStatus.HELD)).update(
        status=OutreachStatus.SKIPPED, error_code="admin", updated_at=timezone.now()
    )


def hold_messages(queryset) -> int:
    return queryset.filter(status=OutreachStatus.QUEUED).update(
        status=OutreachStatus.HELD, error_code="admin_hold", updated_at=timezone.now()
    )


def release_messages(queryset) -> int:
    return queryset.filter(status=OutreachStatus.HELD).update(
        status=OutreachStatus.QUEUED, error_code="", updated_at=timezone.now()
    )
