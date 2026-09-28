from django.db import models


class ScrapeExport(models.Model):
    alias = models.CharField(max_length=160, db_index=True)
    key = models.CharField(max_length=128, blank=True)
    tier = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=64)
    deliverable_met = models.BooleanField(default=False)
    chart_confidence = models.FloatField(default=0.0)
    chart_headers = models.JSONField(default=list)
    chart_rows = models.JSONField(default=list)
    lxml_path = models.TextField(blank=True)
    selenium_xpath = models.TextField(blank=True)
    selenium_css = models.TextField(blank=True)
    egress_ip = models.CharField(max_length=64, blank=True)
    proxy_type = models.CharField(max_length=32, blank=True)
    html_path = models.CharField(max_length=512, blank=True)
    screenshot_path = models.CharField(max_length=512, blank=True)
    inspect_hits = models.JSONField(default=list)
    raw_artifact = models.JSONField(default=dict)
    scraped_at = models.DateTimeField(null=True, blank=True)
    artifact_path = models.CharField(max_length=512, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scraped_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.alias} t{self.tier} {self.outcome}"
