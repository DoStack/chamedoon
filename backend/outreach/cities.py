"""The six launch cities, and whose clock a message goes by.

A place counts when `HomeChannel` says so: the metro's name or one of its aliases (suburbs, other spellings).
The listing's own city wins over the group it was posted in; only an empty city falls back to the group's.
"""

from __future__ import annotations

from datetime import datetime

from home.alerts import city_zone
from home.models import HomeChannel, HomeListing
from home.publish import route
from outreach import conf

LAUNCH_CITIES = {
    ("CA", "toronto"),
    ("CA", "montreal"),
    ("CA", "vancouver"),
    ("DE", "berlin"),
    ("DE", "hamburg"),
    ("DE", "cologne"),
}


def launch_channels() -> list[HomeChannel]:
    return [channel for channel in HomeChannel.objects.filter(enabled=True).exclude(city="") if (channel.country, channel.city.casefold()) in LAUNCH_CITIES]


def metro_of_name(country: str, name: str, channels: list[HomeChannel] | None = None) -> HomeChannel | None:
    channels = launch_channels() if channels is None else channels
    wanted = (name or "").strip().casefold()
    if not wanted:
        return None
    for channel in channels:
        if channel.country == (country or "").upper() and wanted in channel.names():
            return channel
    return None


def metro_of_listing(listing: HomeListing, channels: list[HomeChannel] | None = None) -> HomeChannel | None:
    """The listing's launch metro, or None. A listing with a city outside the six stays outside, whatever its group."""
    channels = launch_channels() if channels is None else channels
    found = route(listing, channels)
    if found is not None:
        return found
    if (listing.city or "").strip() or (listing.area or "").strip():
        return None
    source = listing.post.source
    return metro_of_name(source.country, source.default_city, channels)


def in_send_window(country: str, city: str, now: datetime) -> bool:
    start, end = conf.send_window()
    return start <= now.astimezone(city_zone(country, city)).hour < end
