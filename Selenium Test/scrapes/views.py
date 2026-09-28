"""DRF export/ingest — does not drive Selenium."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from scrapes.models import ScrapeExport
from scrapes.serializers import ScrapeExportDetailSerializer, ScrapeExportSerializer


def _artifacts_dir() -> Path:
    return Path(settings.SCRAPE_ARTIFACTS_DIR)


def _ingest_payload(path: Path, payload: dict) -> ScrapeExport:
    scraped_at = payload.get("scraped_at")
    when = parse_datetime(scraped_at) if isinstance(scraped_at, str) else None
    if when is None and isinstance(scraped_at, str):
        try:
            when = datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
        except ValueError:
            when = None
    defaults = {
        "alias": payload.get("alias") or payload.get("key") or path.stem,
        "key": payload.get("key") or "",
        "tier": int(payload.get("tier") or 0),
        "outcome": payload.get("outcome") or "error",
        "deliverable_met": bool(payload.get("deliverable_met")),
        "chart_confidence": float(payload.get("chart_confidence") or 0),
        "chart_headers": payload.get("chart_headers") or [],
        "chart_rows": payload.get("chart_rows") or [],
        "lxml_path": payload.get("lxml_path") or "",
        "selenium_xpath": payload.get("selenium_xpath") or "",
        "selenium_css": payload.get("selenium_css") or "",
        "egress_ip": payload.get("egress_ip") or "",
        "proxy_type": payload.get("proxy_type") or "",
        "html_path": payload.get("html_path") or "",
        "screenshot_path": payload.get("screenshot_path") or "",
        "inspect_hits": payload.get("inspect_hits") or [],
        "raw_artifact": payload,
        "scraped_at": when,
    }
    obj, _ = ScrapeExport.objects.update_or_create(artifact_path=str(path), defaults=defaults)
    return obj


class ExportListView(APIView):
    def get(self, request: Request) -> Response:
        qs = ScrapeExport.objects.all()
        return Response(ScrapeExportSerializer(qs, many=True).data)


class DeliverableListView(APIView):
    def get(self, request: Request) -> Response:
        qs = ScrapeExport.objects.filter(deliverable_met=True)
        return Response(ScrapeExportSerializer(qs, many=True).data)


class ExportDetailView(APIView):
    def get(self, request: Request, alias: str) -> Response:
        qs = ScrapeExport.objects.filter(alias=alias)
        if not qs.exists():
            qs = ScrapeExport.objects.filter(key=alias)
        if not qs.exists():
            return Response({"detail": f"No export for {alias}"}, status=status.HTTP_404_NOT_FOUND)
        return Response(ScrapeExportDetailSerializer(qs, many=True).data)


class IngestView(APIView):
    def post(self, request: Request) -> Response:
        pattern = (request.data or {}).get("glob") or "**/*.json"
        root = _artifacts_dir()
        if not root.exists():
            return Response(
                {"ingested": 0, "detail": f"Artifacts dir missing: {root}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        created = []
        errors = []
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path.suffix != ".json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or "outcome" not in payload:
                    continue
                obj = _ingest_payload(path, payload)
                created.append(obj.id)
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
        return Response({"ingested": len(created), "ids": created, "errors": errors})
