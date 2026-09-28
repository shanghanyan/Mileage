"""Mode 2: Screenshot + LLaVA via Ollama."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import requests

from extraction.prompts import prompt_for_url

log = logging.getLogger(__name__)


class VisionExtractor:
    def __init__(
        self,
        enabled: bool,
        model: str = "llava",
        host: str = "http://localhost:11434",
        timeout: int = 30,
    ):
        self.enabled = enabled
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    @staticmethod
    def check_ollama(host: str = "http://localhost:11434") -> tuple[bool, bool]:
        """Returns (ollama_running, llava_available)."""
        try:
            resp = requests.get(f"{host.rstrip('/')}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False, False
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            llava = any("llava" in name for name in models)
            return True, llava
        except Exception:
            return False, False

    async def extract(
        self,
        page,
        url: str,
        screenshots_dir: Path,
        page_id: int,
    ) -> tuple[dict[str, Any], bool, str | None, Path | None]:
        if not self.enabled:
            return {}, False, "vision_disabled", None

        screenshot_path = screenshots_dir / f"page_{page_id:05d}.png"
        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
            data = self._query_llava(screenshot_path, prompt_for_url(url))
            if data:
                return data, True, None, screenshot_path
            return {}, False, "empty_vision_response", screenshot_path
        except Exception as e:
            return {}, False, str(e), screenshot_path

    def _query_llava(self, image_path: Path, context_prompt: str) -> dict[str, Any]:
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        payload = {
            "model": self.model,
            "prompt": (
                f"{context_prompt}\n\n"
                "After analyzing the image, respond with ONLY valid JSON (no markdown fences)."
            ),
            "images": [image_b64],
            "stream": False,
            "format": "json",
        }
        resp = requests.post(
            f"{self.host}/api/generate",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {k: v for k, v in parsed.items() if v is not None}
        return {}
