"""Bring areas, cities and listing links in line with `home.area_catalog`. No model, rules only. Never deletes a row.

    manage.py home_areas_sync --dry-run [--report FILE]   reads, writes nothing, in a read-only transaction
    manage.py home_areas_sync --apply [--report FILE]     back up home_homearea and home_homelisting first

Safe to run twice. Saves go past the signals, so the job does not relink everything a second time.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from home.area_catalog import CATALOG
from home.areas import AreaIndex, _metro_key
from home.city_names import city_fa, clean_city, is_place
from home.feed import city_names
from home.models import HomeArea, HomeChannel, HomeListing, ListingStatus
from home.publish import city_channels, route

BATCH = 500
SAMPLE = 30
SHOWN_UNMATCHED = 60


def _is_shown(listing: HomeListing) -> bool:
    return listing.status == ListingStatus.ACTIVE and not listing.needs_review


class Sync:
    def __init__(self) -> None:
        self.channels = city_channels()
        self.rows = list(HomeArea.objects.all())
        self.added: list[HomeArea] = []
        self.corrected: list[tuple[HomeArea, list[str]]] = []

    def plan_areas(self) -> None:
        """Change the rows in memory only: the dry run must see the catalog without writing it."""
        known = {(row.country, _metro_key(row.city), row.canonical): row for row in self.rows}
        for (country, city), entries in CATALOG.items():
            for rank, (canonical, fa, group, lat, lng, aliases) in enumerate(entries):
                wanted = {"fa": fa, "group": group, "lat": lat, "lng": lng, "aliases": aliases, "rank": rank}
                row = known.get((country, _metro_key(city), canonical))
                if row is None:
                    row = HomeArea(country=country, city=city, canonical=canonical, **wanted)
                    self.rows.append(row)
                    self.added.append(row)
                    continue
                changed = [name for name, value in wanted.items() if _differs(getattr(row, name), value)]
                if changed:
                    for name in changed:
                        setattr(row, name, wanted[name])
                    self.corrected.append((row, changed))

    def indexes(self) -> dict[int, AreaIndex]:
        by_metro: dict[tuple[str, str], list[HomeArea]] = defaultdict(list)
        for row in self.rows:
            by_metro[(row.country, _metro_key(row.city))].append(row)
        for rows in by_metro.values():
            rows.sort(key=lambda row: (row.rank, row.canonical))
        return {channel.pk: AreaIndex(channel, by_metro[(channel.country, _metro_key(channel.city))]) for channel in self.channels}

    def write_areas(self) -> None:
        HomeArea.objects.bulk_create(self.added)
        for row in self.added:
            if row.pk is None:  # bulk_create fills pks on Postgres; a backend that does not gets them read back
                row.pk = HomeArea.objects.get(country=row.country, city=row.city, canonical=row.canonical).pk
        for row, changed in self.corrected:
            HomeArea.objects.filter(pk=row.pk).update(**{name: getattr(row, name) for name in changed})


def _differs(have, want) -> bool:
    if isinstance(want, float):
        return have is None or abs(float(have) - want) > 0.00005
    return have != want


def _before_metro(channels: list[HomeChannel], listing: HomeListing) -> str:
    channel = route(listing, channels)
    return channel.city if channel else ""


class Command(BaseCommand):
    help = "Upsert the area catalog, repair city values and relink listings. Rules only. Deletes nothing."

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--report", default="", help="Write the markdown report to this file instead of stdout.")
        parser.add_argument("--seed", type=int, default=20261104, help="For the sample of matches.")

    def handle(self, *args, **options) -> None:
        with transaction.atomic():
            if options["dry_run"] and connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            report = self.run(apply=options["apply"], seed=options["seed"])
        if options["report"]:
            with open(options["report"], "w", encoding="utf-8") as out:
                out.write(report)
            self.stdout.write(f"Report: {options['report']}")
        else:
            self.stdout.write(report)

    def run(self, *, apply: bool, seed: int) -> str:
        sync = Sync()
        if not sync.channels:
            raise CommandError("No enabled city channels: nothing to route listings by.")
        sync.plan_areas()
        indexes = sync.indexes()
        if apply:
            sync.write_areas()
        channels, now, names = sync.channels, timezone.now(), city_names()

        before: dict[str, Counter] = defaultdict(Counter)
        after: dict[str, Counter] = defaultdict(Counter)
        unmatched: dict[str, Counter] = defaultdict(Counter)
        city_changes: Counter = Counter()
        matches: list[tuple] = []
        changed_rows: list[HomeListing] = []
        moved_out: list[tuple] = []
        no_fa: Counter = Counter()
        totals: Counter = Counter()
        moved_links: Counter = Counter()

        listings = HomeListing.objects.select_related("post__source", "home_area").order_by("pk")
        for listing in listings.iterator(chunk_size=BATCH):
            totals["listings"] += 1
            shown = _is_shown(listing)
            old = (listing.city, listing.area, listing.country, listing.home_area)
            old_id = listing.home_area_id
            old_metro = _before_metro(channels, listing)
            if shown and old_metro:
                before[old_metro]["active"] += 1
                before[old_metro]["text"] += 1 if listing.area else 0
                before[old_metro]["linked"] += 1 if listing.home_area_id else 0

            city, area, country = clean_city(listing.city, listing.area, listing.country, listing.post.source.default_city)
            listing.city, listing.area, listing.country = city, area, country
            if (city, area, country) != old[:3]:
                totals["cities_repaired"] += 1
                city_changes[(old[2], old[0], old[1], country, city, area)] += 1
            channel = route(listing, channels)
            metro = channel.city if channel else ""
            if shown and old_metro and metro != old_metro:
                moved_out.append((listing.pk, old[0], old[1], city, metro or "-"))
            found = indexes[channel.pk].match(area, city) if channel else None
            if channel and not indexes[channel.pk].rows:
                found = old[3]  # no rows for this metro yet: leave it as it is

            if (found is None) != (old_id is None) or (found is not None and found.pk != old_id):
                totals["links_changed"] += 1
                if shown and old_id is not None:
                    moved_links[(old[3].city, old[1], old[3].canonical, found.canonical if found else "-")] += 1
            listing.home_area = found
            listing.area_checked_at = now
            changed_rows.append(listing)

            if shown and metro:
                after[metro]["active"] += 1
                after[metro]["text"] += 1 if is_place(area) else 0
                after[metro]["linked"] += 1 if found else 0
                if found is None and is_place(area):
                    unmatched[metro][area] += 1
                elif found is not None:
                    matches.append((metro, city, area, found.canonical, found.fa))
            if shown and city and not city_fa(city) and city.casefold() not in names:
                no_fa[city] += 1

            if apply and len(changed_rows) >= BATCH:
                HomeListing.objects.bulk_update(changed_rows, ["city", "area", "country", "home_area", "area_checked_at"])
                changed_rows = []
        if apply and changed_rows:
            HomeListing.objects.bulk_update(changed_rows, ["city", "area", "country", "home_area", "area_checked_at"])

        return self.render(sync, before, after, unmatched, city_changes, matches, moved_out, moved_links, no_fa, totals, seed=seed, applied=apply)

    def render(self, sync, before, after, unmatched, city_changes, matches, moved_out, moved_links, no_fa, totals, *, seed, applied) -> str:
        out = [f"# Areas sync {'applied' if applied else 'dry run'}", ""]
        out += [
            f"Listings read: {totals['listings']}. Area rows now: {len(sync.rows)} ({len(sync.added)} new, {len(sync.corrected)} corrected, none deleted). "
            f"Listings with a repaired city or area text: {totals['cities_repaired']}. Links changed: {totals['links_changed']}.",
            "",
            "## Active listings per metro",
            "",
            "| Metro | Active | Area text before | Linked before | Area text after | Linked after | Unmatched text |",
            "|---|---|---|---|---|---|---|",
        ]
        for metro in sorted(set(before) | set(after)):
            b, a = before[metro], after[metro]
            out.append(f"| {metro} | {a['active']} | {b['text']} | {b['linked']} | {a['text']} | {a['linked']} | {sum(unmatched[metro].values())} |")
        out += ["", "## Unmatched area text, active listings, most common first", ""]
        for metro in sorted(unmatched):
            shown = unmatched[metro].most_common(SHOWN_UNMATCHED)
            more = len(unmatched[metro]) - len(shown)
            out.append(f"**{metro}**: " + "; ".join(f"{text} ({count})" for text, count in shown) + (f"; and {more} more" if more > 0 else ""))
            out.append("")
        out += [f"## {SAMPLE} random matches", "", "| Metro | City | Area text | Row | Persian |", "|---|---|---|---|---|"]
        generator = random.Random(seed)
        named = [row for row in matches if row[2]]
        for metro, city, area, canonical, fa in generator.sample(named, min(SAMPLE, len(named))):
            out.append(f"| {metro} | {city} | {area} | {canonical} | {fa} |")
        out += ["", "## Active links that move or go, an old link to another row or none", "", "| Metro | Area text | Was | Becomes | Rows |", "|---|---|---|---|---|"]
        for (metro, area, was, now, ), count in sorted(moved_links.items(), key=lambda item: (item[0][0], -item[1])):
            out.append(f"| {metro} | {area} | {was} | {now} | {count} |")
        out += ["", "## Repaired city values", "", "| Was | Becomes | Rows |", "|---|---|---|"]
        for (old_country, old_city, old_area, country, city, area), count in sorted(city_changes.items(), key=lambda item: -item[1]):
            out.append(f"| {old_country} {old_city!r} / {old_area!r} | {country} {city!r} / {area!r} | {count} |")
        out += ["", "## Active listings that leave a metro's feed", ""]
        out += [f"- {pk}: {city!r} / {area!r} now {new_city!r}, metro {metro}" for pk, city, area, new_city, metro in moved_out] or ["None."]
        out += ["", f"## Active cities with no Persian name: {len(no_fa)}", ""]
        out += [f"- {city} ({count})" for city, count in no_fa.most_common()] or ["None."]
        return "\n".join(out) + "\n"
