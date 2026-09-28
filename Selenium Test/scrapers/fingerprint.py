"""Honest Linux VPS identity — load vm_profile.json, never spoof Windows."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scrapers.contracts import PROJECT_ROOT

DEFAULT_PROFILE = {
    "machine_id": "vps-scraper-001",
    "hostname": "scraper",
    "os": "Linux",
    "hypervisor": "kvm",
    "browser_primary": "Google Chrome",
    "browser_alt": "Firefox + Tor",
    "screen": {"width": 1920, "height": 1080},
    "locale": "en-US",
    "timezone": "America/Los_Angeles",
    "user_agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}


@dataclass(frozen=True)
class VmProfile:
    machine_id: str
    hostname: str
    os: str
    hypervisor: str
    locale: str
    timezone: str
    user_agent: str
    screen_width: int
    screen_height: int
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


def direct_proxy_type() -> str:
    """Egress label for non-Tor traffic. Override with SCRAPE_PROXY_TYPE (default: vps)."""
    return os.environ.get("SCRAPE_PROXY_TYPE", "vps")


def load_vm_profile(path: Path | None = None) -> VmProfile:
    candidates = [
        path,
        Path(os.environ["VM_PROFILE_PATH"]) if os.environ.get("VM_PROFILE_PATH") else None,
        PROJECT_ROOT / "vm_profile.json",
        PROJECT_ROOT / "vm_profile.example.json",
    ]
    data = dict(DEFAULT_PROFILE)
    for candidate in candidates:
        if candidate and candidate.exists():
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
            data.update(loaded)
            break
    screen = data.get("screen") or {}
    hostname = data.get("hostname") or socket.gethostname()
    return VmProfile(
        machine_id=str(data.get("machine_id") or DEFAULT_PROFILE["machine_id"]),
        hostname=str(hostname),
        os=str(data.get("os") or "Linux"),
        hypervisor=str(data.get("hypervisor") or "kvm"),
        locale=str(data.get("locale") or "en-US"),
        timezone=str(data.get("timezone") or "America/Los_Angeles"),
        user_agent=str(data.get("user_agent") or DEFAULT_PROFILE["user_agent"]),
        screen_width=int(screen.get("width") or 1920),
        screen_height=int(screen.get("height") or 1080),
        raw=data,
    )
