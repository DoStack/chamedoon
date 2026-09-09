from django.core.management.base import BaseCommand

from ai.openrouter import complete, openrouter_enabled, openrouter_model


class Command(BaseCommand):
    help = "Check OpenRouter config, optionally send a tiny live prompt."

    def add_arguments(self, parser):
        parser.add_argument(
            "--live",
            action="store_true",
            help="Call OpenRouter with a short ping prompt (uses free-model quota).",
        )

    def handle(self, *args, **options):
        enabled = openrouter_enabled()
        self.stdout.write(f"enabled: {'yes' if enabled else 'no'}")
        self.stdout.write(f"model: {openrouter_model()}")
        if not enabled:
            self.stderr.write(self.style.WARNING("Set OPENROUTER_API_KEY to enable OpenRouter."))
            return
        if not options.get("live"):
            self.stdout.write("Pass --live to send a test prompt.")
            return
        result = complete("Reply with the single word pong.")
        if not result.ok:
            self.stderr.write(self.style.ERROR(result.error or "OpenRouter request failed."))
            return
        self.stdout.write(f"used_model: {result.model}")
        self.stdout.write(result.text)
