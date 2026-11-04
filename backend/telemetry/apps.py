from django.apps import AppConfig


class TelemetryConfig(AppConfig):
    name = "telemetry"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from django.core.signals import request_finished

        from telemetry import flush

        # On Vercel a function can freeze after its response: give the sender a moment first.
        request_finished.connect(lambda **_: flush(), weak=False, dispatch_uid="telemetry_flush")
