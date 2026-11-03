"""The map's mock data is what the server answers: the Playwright tests of F09 and the Preview read this same file."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from home.models import HomeArea, HomeChannel

FIXTURE = json.loads((Path(__file__).resolve().parent.parent / "home_api" / "testdata" / "map_fixture.json").read_text(encoding="utf-8"))
SAME_AS_SEED = ("canonical", "name_fa", "group", "lat", "lng")


class MapFixtureTests(TestCase):
    def _areas(self) -> dict:
        # The migrations seed Toronto's channel and areas.
        HomeChannel.objects.filter(country="CA", city="Toronto").update(enabled=True)
        response = APIClient().get("/api/v1/public/areas/", {"country": "CA", "city": "toronto"})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_areas_are_the_servers(self) -> None:
        body = self._areas()
        fixture = FIXTURE["areas"]
        self.assertEqual(set(body), set(fixture))
        self.assertEqual((body["country"], body["city"]), (fixture["country"], fixture["city"]))
        served = {row["canonical"]: row for row in body["areas"]}
        for row in fixture["areas"]:
            with self.subTest(area=row["canonical"]):
                self.assertEqual(set(row), set(served[row["canonical"]]))
                self.assertEqual({key: row[key] for key in SAME_AS_SEED}, {key: served[row["canonical"]][key] for key in SAME_AS_SEED})
                for key in ("id", "listing_count", "week_count"):
                    self.assertIsInstance(row[key], int)

    def test_an_area_with_no_point_answers_null_for_both(self) -> None:
        # The case F09 keeps off the map. e2e/map.spec.ts answers with such a row.
        HomeArea.objects.filter(country="CA", city="Toronto", canonical="pickering").update(lat=None, lng=None)
        row = next(row for row in self._areas()["areas"] if row["canonical"] == "pickering")
        self.assertIsNone(row["lat"])
        self.assertIsNone(row["lng"])

    def test_meta_gives_the_style_url_of_settings(self) -> None:
        body = APIClient().get("/api/v1/public/meta/").json()
        self.assertEqual(body["map_style_url"], settings.HOME_MAP_STYLE_URL)

    def test_the_default_style_is_the_one_in_the_fixture(self) -> None:
        self.assertEqual(settings.HOME_MAP_STYLE_URL, FIXTURE["map_style_url"])
