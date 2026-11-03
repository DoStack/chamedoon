"""Back office v3 (Admin3 boards), behind `HomeSettings.admin_v3`: search queries, the AI search page, the bot message log.

With the flag off these pages answer 404, their models leave the admin index and the sidebar, and nothing else changes.
A replay never messages anyone. A model replay obeys the emergency stop and the caps and counts like any other call.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.views.decorators.http import require_http_methods
from unfold.admin import ModelAdmin

from home import botparse, conf
from home.alerts import search as search_listings
from home.areas import mark_areas_changed, metro_rows
from home.models import HomeArea, HomeBotMessage, HomeChannel, HomeSearchQuery, HomeSettings, SearchQueryStatus

STATUS_COLOR = {"ok": "green", "zero": "red", "low": "orange", "fallback": "orange", "capped": "orange"}
MODELS = ("gpt-5-mini", "gpt-5-nano", botparse.SIMPLE)
RANGES = {"today": ("Today", 1), "7": ("Past 7 days", 7), "14": ("Past 14 days", 14)}
LOW = botparse.LOW_CONFIDENCE
SAMPLE = 2000
EXPORT_LIMIT = 20000
FIELD_ORDER = ("city", "home_type", "budget", "gender", "move_in_date", "area")


def require_on() -> None:
    if not conf.admin_v3_on():
        raise Http404


def csv_cell(value) -> str:
    """Text people typed goes into a spreadsheet: a leading = + - or @ must not run as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


def csv_response(name: str, header: list[str], rows) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_cell(cell) for cell in row])
    return response


def tag(text: str, color: str):
    colors = {"green": "#166534", "red": "#b91c1c", "orange": "#9a3412", "blue": "#1d4ed8", "purple": "#6d28d9", "grey": "#3f3f46"}
    return format_html('<span style="font-weight:600;color:{}">{}</span>', colors.get(color, "#3f3f46"), text)


def user_link(telegram_id: int):
    url = reverse("admin:users_user_changelist") + f"?q={int(telegram_id)}"
    return format_html('<a href="{}">{}</a>', url, telegram_id)


class V3Admin(ModelAdmin):
    """Read only, and hidden while the flag is off. Deleting stays, for what the retention has not reached."""

    def has_module_permission(self, request) -> bool:
        return conf.admin_v3_on() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None) -> bool:
        return conf.admin_v3_on() and super().has_view_permission(request, obj)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return conf.admin_v3_on() and super().has_delete_permission(request, obj)


class ZeroFilter(admin.SimpleListFilter):
    title = "zero results"
    parameter_name = "zero"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(results_count=0)
        if self.value() == "no":
            return queryset.exclude(results_count=0)
        return queryset


class ConfidenceFilter(admin.SimpleListFilter):
    title = "low confidence"
    parameter_name = "low"

    def lookups(self, request, model_admin):
        return (("yes", f"Below {LOW}"), ("no", f"{LOW} and above"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(confidence__lt=LOW)
        if self.value() == "no":
            return queryset.filter(confidence__gte=LOW)
        return queryset


@admin.register(HomeSearchQuery)
class HomeSearchQueryAdmin(V3Admin):
    list_display = ("created_at", "person", "raw_text_short", "city", "parser_tag", "confidence_value", "results_count", "latency_ms", "status_tag", "replay_link")
    list_filter = (("created_at", admin.DateFieldListFilter), "city", "parser", ZeroFilter, ConfidenceFilter)
    search_fields = ("raw_text", "=telegram_user_id")
    date_hierarchy = None
    list_per_page = 50
    actions = ("replay_selected", "mark_reviewed", "export_selected")
    fields = ("raw_text", "person", "created_at", "parser_tag", "latency_ms", "results_line", "clicked_listing_ids", "parsed_json", "field_table", "replay_link", "reviewed_at")
    readonly_fields = fields

    @admin.display(description="User", ordering="telegram_user_id")
    def person(self, obj):
        return user_link(obj.telegram_user_id)

    @admin.display(description="Raw text")
    def raw_text_short(self, obj):
        return format_html('<span dir="auto">{}</span>', obj.raw_text[:90])

    @admin.display(description="Parser", ordering="parser")
    def parser_tag(self, obj):
        return tag(obj.parser or "—", "grey" if obj.parser == botparse.SIMPLE else "purple")

    @admin.display(description="Confidence", ordering="confidence")
    def confidence_value(self, obj):
        if obj.confidence is None:
            return "—"
        return tag(f"{obj.confidence:.2f}", "red") if obj.confidence < LOW else f"{obj.confidence:.2f}"

    @admin.display(description="Status", ordering="status")
    def status_tag(self, obj):
        return tag(obj.get_status_display(), STATUS_COLOR.get(obj.status, "grey"))

    @admin.display(description="Results count")
    def results_line(self, obj):
        if obj.results_count == 0:
            return format_html("0 {}", tag("Zero results", "red"))
        return "—" if obj.results_count is None else obj.results_count

    @admin.display(description="Parsed JSON")
    def parsed_json(self, obj):
        return format_html('<pre dir="ltr" style="white-space:pre-wrap">{}</pre>', json.dumps(obj.parsed or {}, ensure_ascii=False, indent=2))

    @admin.display(description="Field confidence")
    def field_table(self, obj):
        scores = obj.field_confidence or {}
        if not scores:
            return "None: the simple parser does not score fields."
        rows = format_html_join("", "<tr><td>{}</td><td>{}</td></tr>", ((name, f"{float(value):.2f}") for name, value in scores.items() if isinstance(value, (int, float))))
        return format_html("<table><tr><th>Field</th><th>Score</th></tr>{}</table>", rows)

    @admin.display(description="")
    def replay_link(self, obj):
        return format_html('<a href="{}">Replay</a>', reverse("admin:home_query_replay", args=[obj.pk]))

    @admin.action(description="Replay selected queries (simple parser, no cost)")
    def replay_selected(self, request, queryset):
        changed = total = 0
        for query in queryset[:200]:
            total += 1
            changed += int(replay(query, botparse.SIMPLE)["changed"])
        self.message_user(request, f"Replayed {total}: {changed} read differently now. Nothing was saved and nobody was messaged.", messages.SUCCESS)

    @admin.action(description="Mark as reviewed")
    def mark_reviewed(self, request, queryset):
        self.message_user(request, f"Marked {queryset.update(reviewed_at=timezone.now())} as reviewed.", messages.SUCCESS)

    @admin.action(description="Export selected as CSV")
    def export_selected(self, request, queryset):
        return export_queries(queryset)

    def get_urls(self):
        extra = [
            path("<int:pk>/replay/", self.admin_site.admin_view(replay_view), name="home_query_replay"),
            path("export/", self.admin_site.admin_view(queries_export_view), name="home_query_export"),
        ]
        return extra + super().get_urls()


@admin.register(HomeBotMessage)
class HomeBotMessageAdmin(V3Admin):
    list_display = ("created_at", "person", "direction", "kind", "excerpt", "query_link")
    list_filter = (("created_at", admin.DateFieldListFilter), "direction", "kind", ("query", admin.EmptyFieldListFilter))
    search_fields = ("text", "=telegram_user_id")
    list_per_page = 50
    actions = ("export_selected",)
    fields = ("created_at", "person", "direction", "kind", "text", "query_link")
    readonly_fields = fields

    @admin.display(description="User", ordering="telegram_user_id")
    def person(self, obj):
        url = reverse("admin:home_conversation", args=[obj.telegram_user_id])
        return format_html('<a href="{}">{}</a>', url, obj.telegram_user_id)

    @admin.display(description="Text excerpt")
    def excerpt(self, obj):
        return format_html('<span dir="auto">{}</span>', obj.text[:120])

    @admin.display(description="Query")
    def query_link(self, obj):
        if not obj.query_id:
            return "—"
        return format_html('<a href="{}">Query {}</a>', reverse("admin:home_homesearchquery_change", args=[obj.query_id]), obj.query_id)

    @admin.action(description="Export selected as CSV")
    def export_selected(self, request, queryset):
        rows = queryset.values_list("created_at", "telegram_user_id", "direction", "kind", "text", "query_id")[:EXPORT_LIMIT]
        return csv_response("bot-messages", ["time", "user", "direction", "kind", "text", "query"], rows)

    def get_urls(self):
        return [path("conversation/<int:telegram_id>/", self.admin_site.admin_view(conversation_view), name="home_conversation"), *super().get_urls()]


def export_queries(queryset) -> HttpResponse:
    rows = queryset.values_list("created_at", "telegram_user_id", "raw_text", "city", "parser", "confidence", "results_count", "latency_ms", "status")[:EXPORT_LIMIT]
    return csv_response("search-queries", ["created", "user", "raw text", "city", "parser", "confidence", "results", "latency ms", "status"], rows)


def _channel_of(query: HomeSearchQuery) -> HomeChannel | None:
    return HomeChannel.objects.filter(country=query.country, city=query.city, enabled=True).first()


REPLAY_REFUSED = {
    botparse.OFF: "AI search is switched off or stopped, so no call went out. Regex still replays.",
    botparse.CAPPED: "Today's AI cap is reached, so no call went out. Regex still replays.",
}
COMPARED = ("types", "rooms", "areas", "unmapped_areas", "max", "move", "gender", "term", "furnished", "bills", "results")


def _results_for(fields: dict, query: HomeSearchQuery, channel: HomeChannel) -> int:
    from home.botv2 import criteria

    return len(search_listings(criteria({key: value for key, value in fields.items() if key != "unmapped_areas"}, query.telegram_user_id, channel)))


def replay(query: HomeSearchQuery, model: str) -> dict:
    """Read the same text again. Saves nothing and messages nobody."""
    channel = _channel_of(query)
    original = {**(query.parsed or {}), "results": query.results_count}
    if channel is None:
        return {"error": "This city has no channel now.", "original": original, "lines": [], "changed": False}
    if model == botparse.SIMPLE:
        parsed = botparse.parse_simple(query.raw_text, channel)
    else:
        try:
            parsed = botparse.ask_ai(query.raw_text, channel, model=model)
        except botparse.AiRefused as refused:
            return {"error": REPLAY_REFUSED[refused.reason], "original": original, "lines": [], "changed": False}
        except (TimeoutError, ValueError, TypeError, KeyError, OSError) as exc:
            return {"error": f"The model did not answer: {type(exc).__name__}", "original": original, "lines": [], "changed": False}
    fields = {**parsed.fields, "unmapped_areas": parsed.unmapped}
    fields["results"] = _results_for(fields, query, channel)
    lines = []
    for name in COMPARED:
        before, after = original.get(name), fields.get(name)
        if before in (None, [], "") and after in (None, [], ""):
            continue
        lines.append({"name": name, "before": json.dumps(before, ensure_ascii=False), "after": json.dumps(after, ensure_ascii=False), "changed": before != after})
    return {"error": "", "lines": lines, "changed": any(line["changed"] for line in lines), "parser": parsed.parser}


@require_http_methods(["GET", "POST"])
def replay_view(request, pk: int):
    require_on()
    if not request.user.has_perm("home.view_homesearchquery"):
        raise PermissionDenied
    query = get_object_or_404(HomeSearchQuery, pk=pk)
    result = None
    model = request.POST.get("model") or MODELS[0]
    if request.method == "POST":
        if model not in MODELS:
            raise Http404
        result = replay(query, model)
    context = {**admin.site.each_context(request), "title": "Replay search query", "query": query, "models": MODELS, "model": model, "result": result}
    return render(request, "admin/home_query_replay.html", context)


def queries_export_view(request):
    require_on()
    if not request.user.has_perm("home.view_homesearchquery"):
        raise PermissionDenied
    return export_queries(HomeSearchQuery.objects.all())


def conversation_view(request, telegram_id: int):
    require_on()
    if not request.user.has_perm("home.view_homebotmessage"):
        raise PermissionDenied
    rows = HomeBotMessage.objects.filter(telegram_user_id=telegram_id).select_related("query").order_by("created_at", "id")
    days = sorted({timezone.localtime(row).date() for row in rows.values_list("created_at", flat=True)}, reverse=True)[:30]
    chosen = next((day for day in days if day.isoformat() == request.GET.get("day")), days[0] if days else None)
    shown = [row for row in rows if chosen and timezone.localtime(row.created_at).date() == chosen]
    context = {
        **admin.site.each_context(request),
        "title": f"Conversation · user {telegram_id}",
        "telegram_id": telegram_id,
        "days": days,
        "chosen": chosen,
        "rows": shown,
        "user_url": reverse("admin:users_user_changelist") + f"?q={telegram_id}",
    }
    return render(request, "admin/home_conversation.html", context)


# ---------------------------------------------------------------------------
# The AI search page.


class AiSearchForm(forms.ModelForm):
    search_model = forms.ChoiceField(choices=[("", "gpt-5-mini (default)"), ("gpt-5-mini", "gpt-5-mini"), ("gpt-5-nano", "gpt-5-nano")], required=False)

    class Meta:
        model = HomeSettings
        fields = ("ai_search_enabled", "search_model", "ai_search_user_cap", "ai_search_global_cap", "ai_search_timeout_seconds", "search_log_retention_days", "ai_search_emergency_stop")


def percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * share) - 1)]


def _rate(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def period_numbers(start: datetime, end: datetime) -> dict:
    rows = HomeSearchQuery.objects.filter(created_at__gte=start, created_at__lt=end)
    total = rows.count()
    latencies = list(rows.exclude(latency_ms=None).values_list("latency_ms", flat=True)[:SAMPLE])
    return {
        "total": total,
        "zero_rate": _rate(rows.filter(results_count=0).count(), total),
        "confidence": rows.aggregate(value=Avg("confidence"))["value"],
        "fallback_rate": _rate(rows.filter(status=SearchQueryStatus.FALLBACK).count(), total),
        "cost": rows.aggregate(value=Sum("cost_usd"))["value"],
        "p95": percentile(latencies, 0.95),
    }


def change(now_value, before_value, *, digits: int = 1):
    if now_value is None or before_value is None:
        return None
    return round(float(now_value) - float(before_value), digits)


def daily_bars(now: datetime) -> list[dict]:
    counts: Counter = Counter()
    start = (now - timedelta(days=13)).replace(hour=0, minute=0, second=0, microsecond=0)
    for created in HomeSearchQuery.objects.filter(created_at__gte=start).values_list("created_at", flat=True):
        counts[timezone.localtime(created).date()] += 1
    days = [(timezone.localtime(start) + timedelta(days=index)).date() for index in range(14)]
    peak = max(counts.values(), default=0) or 1
    return [{"day": day, "count": counts[day], "height": round(counts[day] * 100 / peak)} for day in days]


def field_scores(rows) -> list[dict]:
    totals: defaultdict = defaultdict(list)
    for scores in rows.exclude(field_confidence={}).values_list("field_confidence", flat=True)[:SAMPLE]:
        for name, value in (scores or {}).items():
            if isinstance(value, (int, float)):
                totals[name].append(float(value))
    ordered = [*[name for name in FIELD_ORDER if name in totals], *sorted(set(totals) - set(FIELD_ORDER))]
    return [{"name": name, "score": round(sum(totals[name]) / len(totals[name]), 2), "width": round(sum(totals[name]) / len(totals[name]) * 100)} for name in ordered]


def model_rows(rows) -> list[dict]:
    grouped: defaultdict = defaultdict(list)
    costs: Counter = Counter()
    for parser, latency, cost in rows.values_list("parser", "latency_ms", "cost_usd")[:SAMPLE]:
        grouped[parser or "—"].append(latency or 0)
        costs[parser or "—"] += float(cost or 0)
    return [
        {"model": name, "count": len(values), "cost": round(costs[name], 4), "average": round(sum(values) / len(values)), "p95": percentile(values, 0.95)}
        for name, values in sorted(grouped.items(), key=lambda item: -len(item[1]))
    ]


def unmapped_rows(rows, limit: int = 15) -> list[dict]:
    found: dict = {}
    for created, city, country, parsed in rows.order_by("-created_at").values_list("created_at", "city", "country", "parsed")[:SAMPLE]:
        for raw in (parsed or {}).get("unmapped_areas") or []:
            key = (city, str(raw))
            entry = found.setdefault(key, {"raw": str(raw), "city": city, "country": country, "count": 0, "last": created})
            entry["count"] += 1
    ordered = sorted(found.values(), key=lambda item: -item["count"])[:limit]
    for entry in ordered:
        channel = HomeChannel.objects.filter(country=entry["country"], city=entry["city"]).first()
        entry["areas"] = metro_rows(channel) if channel is not None else []
    return ordered


@require_http_methods(["GET", "POST"])
def ai_search_view(request):
    require_on()
    if not request.user.has_perm("home.view_homesearchquery"):
        raise PermissionDenied
    row = HomeSettings.load()
    form = AiSearchForm(instance=row)
    if request.method == "POST":
        if not request.user.has_perm("home.change_homesettings"):
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "stop":
            HomeSettings.objects.filter(pk=row.pk).update(ai_search_emergency_stop=True)
            messages.warning(request, "Emergency stop is on: no AI search call goes out. The simple parser answers.")
            return redirect("admin:home_ai_search")
        if action == "map":
            return _map_area(request)
        form = AiSearchForm(request.POST, instance=row)
        if form.is_valid():
            form.save()
            messages.success(request, "Saved.")
            return redirect("admin:home_ai_search")
    key = request.GET.get("range") if request.GET.get("range") in RANGES else "today"
    days = RANGES[key][1]
    now = timezone.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    current = period_numbers(start, now)
    before = period_numbers(start - timedelta(days=days), start)
    rows = HomeSearchQuery.objects.filter(created_at__gte=start)
    kpis = {
        **current,
        "total_change": change(current["total"], before["total"], digits=0),
        "zero_change": change(current["zero_rate"], before["zero_rate"]),
        "confidence_change": change(current["confidence"], before["confidence"], digits=2),
        "fallback_change": change(current["fallback_rate"], before["fallback_rate"]),
        "p95_change": change(current["p95"], before["p95"], digits=0),
    }
    context = {
        **admin.site.each_context(request),
        "title": "AI search",
        "ranges": [(name, label) for name, (label, _days) in RANGES.items()],
        "range": key,
        "kpis": kpis,
        "bars": daily_bars(now),
        "fields": field_scores(rows),
        "models": model_rows(rows),
        "unmapped": unmapped_rows(rows),
        "form": form,
        "stopped": row.ai_search_emergency_stop,
    }
    return render(request, "admin/home_ai_search.html", context)


def _map_area(request):
    """«Map to area»: the text people typed becomes an alias of the area, and listings get linked again."""
    if not request.user.has_perm("home.change_homearea"):
        raise PermissionDenied
    raw = (request.POST.get("raw") or "").strip()[:64]
    area = HomeArea.objects.filter(pk=request.POST.get("area") or 0).first()
    if not raw or "," in raw or area is None:
        messages.error(request, "Pick an area. A name with a comma cannot be an alias.")
        return redirect("admin:home_ai_search")
    names = [name.strip() for name in (area.aliases or "").split(",") if name.strip()]
    if raw.casefold() not in {name.casefold() for name in names}:
        area.aliases = ",".join([*names, raw])
        area.save(update_fields=["aliases"])
        mark_areas_changed()
    messages.success(request, f"«{raw}» now means {area.canonical}.")
    return redirect("admin:home_ai_search")
