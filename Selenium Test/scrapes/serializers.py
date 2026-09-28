from rest_framework import serializers

from scrapes.models import ScrapeExport


class ScrapeExportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeExport
        fields = [
            "id",
            "alias",
            "key",
            "tier",
            "outcome",
            "deliverable_met",
            "chart_confidence",
            "chart_headers",
            "chart_rows",
            "lxml_path",
            "selenium_xpath",
            "selenium_css",
            "egress_ip",
            "proxy_type",
            "html_path",
            "screenshot_path",
            "inspect_hits",
            "scraped_at",
            "artifact_path",
        ]


class ScrapeExportDetailSerializer(ScrapeExportSerializer):
    class Meta(ScrapeExportSerializer.Meta):
        fields = ScrapeExportSerializer.Meta.fields + ["raw_artifact"]
