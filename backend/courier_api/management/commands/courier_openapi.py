"""Write courier_api/openapi.yaml from the code, or with --check fail when the file has drifted."""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.plumbing import _load_enum_name_overrides
from drf_spectacular.renderers import OpenApiYamlRenderer
from drf_spectacular.settings import patched_settings

import courier_api
from courier_api.schema import SETTINGS

SCHEMA_FILE = Path(courier_api.__file__).resolve().parent / "openapi.yaml"


def build_schema() -> str:
    # Spectacular caches enum names for the process, so another contract built here must not see this one's, or the reverse.
    _load_enum_name_overrides.cache_clear()
    try:
        with patched_settings(SETTINGS):
            schema = SchemaGenerator(urlconf="courier_api.schema_urls").get_schema(request=None, public=True)
            return OpenApiYamlRenderer().render(schema, renderer_context={}).decode("utf-8")
    finally:
        _load_enum_name_overrides.cache_clear()


class Command(BaseCommand):
    help = "Build the courier API contract."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--check", action="store_true", help="Fail when openapi.yaml differs from the code.")

    def handle(self, *args, **options) -> None:
        built = build_schema()
        if options["check"]:
            if not SCHEMA_FILE.exists() or SCHEMA_FILE.read_text(encoding="utf-8") != built:
                raise CommandError("courier_api/openapi.yaml has drifted: run manage.py courier_openapi and commit it.")
            self.stdout.write("courier_api/openapi.yaml matches the code.")
            return
        SCHEMA_FILE.write_text(built, encoding="utf-8", newline="\n")
        self.stdout.write(f"Wrote {SCHEMA_FILE}")
