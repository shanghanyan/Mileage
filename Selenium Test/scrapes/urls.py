from django.urls import path

from scrapes.views import DeliverableListView, ExportDetailView, ExportListView, IngestView

urlpatterns = [
    path("exports/", ExportListView.as_view(), name="export-list"),
    path("exports/deliverables/", DeliverableListView.as_view(), name="export-deliverables"),
    path("exports/<path:alias>/", ExportDetailView.as_view(), name="export-detail"),
    path("ingest/", IngestView.as_view(), name="ingest"),
]
