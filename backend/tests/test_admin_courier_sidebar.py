"""Every courier admin page is one click away in the sidebar (show_all_applications is off)."""

from __future__ import annotations

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase

COURIER_PAGES = (
    "/admin/courier/couriersettings/",
    "/admin/item_requests/courierchannel/",
    "/admin/courier/courierdigestpost/",
    "/admin/courier/courierintro/",
    "/admin/courier/courierroutealert/",
    "/admin/courier/courieralertsent/",
)


class CourierSidebarTests(TestCase):
    def setUp(self) -> None:
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")
        self.client.login(username="ops", password="ops-pass")

    def test_sidebar_has_a_courier_group_with_every_page(self) -> None:
        text = self.client.get("/admin/").content.decode()
        self.assertIn("Courier", text)
        for path in COURIER_PAGES:
            self.assertIn(path, text)

    def test_every_linked_page_opens(self) -> None:
        for path in COURIER_PAGES:
            self.assertEqual(self.client.get(path).status_code, 200, path)
