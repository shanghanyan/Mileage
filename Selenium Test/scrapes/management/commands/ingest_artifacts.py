"""Load artifact JSON into the export DB without starting the HTTP server."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from scrapes.views import _ingest_payload


class Command(BaseCommand):
    help = "Ingest artifacts/**/*.json into ScrapeExport."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--glob", default="**/*.json", help="Glob under SCRAPE_ARTIFACTS_DIR")

    def handle(self, *args, **options) -> None:
        root = Path(settings.SCRAPE_ARTIFACTS_DIR)
        if not root.exists():
            raise CommandError(f"Artifacts dir missing: {root}")
        ingested = 0
        for path in sorted(root.glob(options["glob"])):
            if not path.is_file() or path.suffix != ".json":
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or "outcome" not in payload:
                continue
            _ingest_payload(path, payload)
            ingested += 1
            self.stdout.write(str(path))
        self.stdout.write(self.style.SUCCESS(f"Ingested {ingested} artifact(s)."))
