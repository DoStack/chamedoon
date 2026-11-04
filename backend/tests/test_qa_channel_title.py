"""Real city channels have no title. The gate must still name one (F11#2), found on the preview copy of real data."""

from __future__ import annotations

from home.models import HomeChannel
from home.public import channel_title
from item_requests.models import City, Country
from tests.test_home_api import ApiCase


class ChannelTitleTests(ApiCase):
    def test_its_own_title_wins(self) -> None:
        self.assertEqual(channel_title(self.channel), "Toronto")

    def test_a_blank_title_gets_the_city_in_persian(self) -> None:
        HomeChannel.objects.filter(pk=self.channel.pk).update(title="")
        self.channel.refresh_from_db()
        country, _ = Country.objects.get_or_create(iso2="CA", defaults={"name_en": "Canada", "name_fa": "کانادا"})
        City.objects.update_or_create(name_en="Toronto", defaults={"name_fa": "تورنتو", "country": country})
        self.assertEqual(channel_title(self.channel), "کانال چمدون - خانه · تورنتو")

    def test_a_blank_title_with_no_persian_name_still_names_the_city(self) -> None:
        channel = HomeChannel.objects.create(country="CA", city="Nowhere", username="c_nowhere", title="")
        self.assertEqual(channel_title(channel), "کانال چمدون - خانه · Nowhere")
