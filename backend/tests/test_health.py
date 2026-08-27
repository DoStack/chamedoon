from django.test import TestCase


class HealthEndpointTests(TestCase):
    def test_health_returns_ok_and_database_connected(self) -> None:
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "koolbar-backend")
        self.assertEqual(payload["database"], "ok")
