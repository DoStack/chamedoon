from __future__ import annotations

import gzip
import json
from collections import Counter
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone

BACKUP_KIND = "chamedoon.db"
BACKUP_VERSION = 1
RESTORE_CONFIRM = "RESTORE"
MAX_BACKUP_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 40 * 1024 * 1024

# Parents first so restore can insert without broken FKs.
DUMP_LABELS = (
    "auth.group",
    "auth.user",
    "item_requests.category",
    "item_requests.country",
    "item_requests.city",
    "users.user",
    "users.botstart",
    "item_requests.itemrequest",
    "market.marketpost",
    "market.marketingeststate",
    "matching.match",
    "matching.matchrating",
    "support.supportticket",
    "support.supportmessage",
    "outreach.outreachstate",
    "outreach.outreachoptout",
    "outreach.outreachmessage",
)


class BackupError(ValueError):
    """Raised when a dump file is missing, too large, or not a Chamedoon backup."""


def _model(label: str):
    app_label, model_name = label.split(".")
    return apps.get_model(app_label, model_name)


def backup_counts() -> dict[str, int]:
    return {label: _model(label).objects.count() for label in DUMP_LABELS}


def build_backup() -> tuple[bytes, str, dict[str, int]]:
    counts: dict[str, int] = {}
    objects: list[dict[str, Any]] = []
    for label in DUMP_LABELS:
        queryset = _model(label).objects.all().order_by("pk")
        counts[label] = queryset.count()
        if counts[label] == 0:
            continue
        objects.extend(json.loads(serializers.serialize("json", queryset)))
    envelope = {
        "kind": BACKUP_KIND,
        "version": BACKUP_VERSION,
        "created_at": timezone.now().isoformat(),
        "counts": counts,
        "objects": objects,
    }
    payload = gzip.compress(json.dumps(envelope, separators=(",", ":")).encode("utf-8"), compresslevel=6)
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    return payload, f"chamedoon-db-{stamp}.json.gz", counts


def parse_backup(data: bytes) -> dict[str, Any]:
    if not data:
        raise BackupError("Choose a .json.gz backup file.")
    if len(data) > MAX_BACKUP_BYTES:
        raise BackupError(f"Backup is larger than {MAX_BACKUP_BYTES // (1024 * 1024)} MB.")
    raw = gzip.decompress(data) if data.startswith(b"\x1f\x8b") else data
    if len(raw) > MAX_UNCOMPRESSED_BYTES:
        raise BackupError("Backup uncompressed size is too large.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("Backup file is not valid JSON.") from exc
    if payload.get("kind") != BACKUP_KIND or int(payload.get("version") or 0) != BACKUP_VERSION:
        raise BackupError("This file is not a Chamedoon database backup.")
    objects = payload.get("objects")
    if not isinstance(objects, list):
        raise BackupError("Backup file is missing objects.")
    return payload


def _reset_sequences() -> None:
    models = [_model(label) for label in DUMP_LABELS]
    statements = connection.ops.sequence_reset_sql(no_style(), models)
    if not statements:
        return
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


@transaction.atomic
def restore_backup(data: bytes, *, keep_user_id: int | None = None) -> dict[str, Any]:
    payload = parse_backup(data)
    StaffUser = get_user_model()
    keeper = StaffUser.objects.filter(pk=keep_user_id).first() if keep_user_id else None
    keeper_fields = None
    if keeper is not None:
        keeper_fields = {
            field.attname: getattr(keeper, field.attname)
            for field in keeper._meta.fields
            if field.attname != "id"
        }

    for label in reversed(DUMP_LABELS):
        queryset = _model(label).objects.all()
        if label == "auth.user" and keep_user_id:
            queryset = queryset.exclude(pk=keep_user_id)
        queryset.delete()

    loaded = 0
    for obj in serializers.deserialize("json", json.dumps(payload["objects"]), ignorenonexistent=True):
        obj.save()
        loaded += 1

    if keep_user_id and keeper_fields is not None:
        staff, created = StaffUser.objects.get_or_create(pk=keep_user_id, defaults=keeper_fields)
        staff.is_active = True
        staff.is_staff = True
        staff.is_superuser = True
        if created:
            for name, value in keeper_fields.items():
                setattr(staff, name, value)
        staff.save()

    _reset_sequences()
    counts = backup_counts()
    return {
        "ok": True,
        "loaded": loaded,
        "created_at": payload.get("created_at"),
        "counts": counts,
        "source_counts": payload.get("counts") or {},
    }


def summarize_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    order = list(DUMP_LABELS)
    rows = [(label, counts.get(label, 0)) for label in order]
    extra = sorted(label for label in counts if label not in order)
    rows.extend((label, counts[label]) for label in extra)
    return rows


def count_total(counts: dict[str, int]) -> int:
    return sum(Counter(counts).values())
