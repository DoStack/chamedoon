from django.core.management.base import BaseCommand

from ai.llm import complete, llm_status


class Command(BaseCommand):
    help = "Check LLM config, optionally send a tiny live prompt."

    def add_arguments(self, parser):
        parser.add_argument(
            "--live",
            action="store_true",
            help="Call the LLM chain with a short ping prompt.",
        )

    def handle(self, *args, **options):
        status = llm_status()
        self.stdout.write(f"enabled: {'yes' if status['enabled'] else 'no'}")
        self.stdout.write(f"openrouter: {'yes' if status['openrouter'] else 'no'}")
        self.stdout.write(f"openai: {'yes' if status['openai'] else 'no'}")
        self.stdout.write(f"models: {status['model']}")
        if not status["enabled"]:
            self.stderr.write(self.style.WARNING("Set OPENROUTER_API_KEY and/or OPENAI_API_KEY."))
            return
        if not options.get("live"):
            self.stdout.write("Pass --live to send a test prompt.")
            return
        result = complete("Reply with the single word pong.")
        if not result.ok:
            self.stderr.write(self.style.ERROR(result.error or "LLM request failed."))
            return
        self.stdout.write(f"used_model: {result.model}")
        self.stdout.write(result.text)
