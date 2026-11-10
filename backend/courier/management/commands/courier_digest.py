from __future__ import annotations

import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from courier import digest


class Command(BaseCommand):
    help = (
        "Daily courier board. `due` prints the scopes whose board is due now. `build --scope ca` writes the numbers "
        "as JSON (stdout or --out); nothing to show records a skip. `send --scope ca --image board.png --data board.json` "
        "posts the rendered board once per local day."
    )

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["due", "build", "send"])
        parser.add_argument("--scope", choices=sorted(digest.SCOPES))
        parser.add_argument("--out", default="")
        parser.add_argument("--image", default="")
        parser.add_argument("--data", default="")
        parser.add_argument("--no-skip", action="store_true", help="build: do not record a skip when there is too little.")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "due":
            self.stdout.write(" ".join(key for key in sorted(digest.SCOPES) if digest.due(key)))
            return
        scope = options["scope"]
        if not scope:
            raise CommandError("--scope is required")
        if action == "build":
            data = digest.build(scope)
            if data is None:
                if not options["no_skip"]:
                    digest.record_skip(scope)
                self.stderr.write(f"{scope}: fewer than {digest.MIN_ITEMS} items, skipped")
                return
            text = json.dumps(data, ensure_ascii=False)
            if options["out"]:
                Path(options["out"]).write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
            return
        if not options["image"] or not options["data"]:
            raise CommandError("send needs --image and --data")
        data = json.loads(Path(options["data"]).read_text(encoding="utf-8"))
        row = digest.send(scope, Path(options["image"]).read_bytes(), data)
        self.stdout.write(f"{scope}: {row.status} message={row.message_id or ''} {row.error}")
        if row.status != "sent":
            raise CommandError(row.error or "not sent")
