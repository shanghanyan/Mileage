import io
import json
import base64
import time
import os
from PIL import Image
from scrapers.base_scraper import get_logger

import ollama

LIFEMILES_PROMPT = (
    "You are a data extraction assistant. This image shows an airline award chart table "
    "for Avianca LifeMiles, used to book United Airlines and other Star Alliance flights. "
    "Extract every row of the table as JSON. "
    "Return ONLY a JSON object — no explanation, no markdown fences. "
    'Schema: {"rows": [{"origin_zone": "string", "destination_zone": "string", '
    '"economy_miles": integer, "business_miles": integer, "first_miles": integer_or_null}]}'
)

TURKISH_PROMPT = (
    "You are a data extraction assistant. This image shows the Turkish Airlines "
    "Miles & Smiles Star Alliance partner award chart for booking flights. "
    "Extract every row as JSON. "
    "Return ONLY a JSON object — no explanation, no markdown fences. "
    'Schema: {"rows": [{"region_from": "string", "region_to": "string", '
    '"economy_miles": integer, "business_miles": integer}]}'
)

KRISFLYER_PROMPT = (
    "You are a data extraction assistant. This image shows the Singapore KrisFlyer "
    "Saver award chart for Star Alliance partner flights. "
    "Extract every row as JSON. "
    "Return ONLY a JSON object — no explanation, no markdown fences. "
    'Schema: {"rows": [{"origin_region": "string", "destination_region": "string", '
    '"economy_saver": integer, "business_saver": integer, "first_saver": integer_or_null}]}'
)


def _response_text(response) -> str:
    """Pull the message content out of an Ollama chat response.

    The ollama client returns either a typed object (``response.message.content``)
    or a plain dict (``response["message"]["content"]``) depending on version, so
    handle both rather than assuming attribute access.
    """
    message = response["message"] if isinstance(response, dict) else response.message
    content = message["content"] if isinstance(message, dict) else message.content
    return content or ""


class VisionScraper:
    def __init__(self) -> None:
        self.host  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.model = os.getenv("OLLAMA_MODEL", "llama3.2-vision:11b")
        self.logger = get_logger("vision_scraper")
        self._client = ollama.Client(host=self.host)

    def is_available(self) -> bool:
        try:
            self._client.list()
            return True
        except Exception:
            return False

    async def extract_table(
        self,
        page,
        crop: tuple[int, int, int, int],
        prompt: str,
        caller_name: str,
    ) -> dict:
        t0 = time.monotonic()
        logger = get_logger(caller_name)
        raw_bytes = await page.screenshot(full_page=True, type="png")
        img = Image.open(io.BytesIO(raw_bytes))

        left, top, width, height = crop
        cropped = img.crop((left, top, left + width, top + height))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        b64_image = base64.b64encode(buf.getvalue()).decode("utf-8")

        logger.info(
            f"vision | caller={caller_name} model={self.model} "
            f"image_kb={len(b64_image) // 1024}"
        )

        raw_text = ""
        for attempt in range(1, 3):
            try:
                response = self._client.chat(
                    model=self.model,
                    messages=[{
                        "role": "user",
                        "content": prompt,
                        "images": [b64_image],
                    }]
                )
                raw_text = _response_text(response).strip()

                if raw_text.startswith("```"):
                    parts = raw_text.split("```")
                    raw_text = parts[1].lstrip("json").strip() if len(parts) > 1 else raw_text

                parsed = json.loads(raw_text)
                duration_ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    f"vision | SUCCESS caller={caller_name} "
                    f"duration_ms={duration_ms} rows={len(parsed.get('rows', []))}"
                )
                return parsed

            except json.JSONDecodeError as exc:
                if attempt == 1:
                    logger.warning(
                        f"vision | JSON parse failed attempt={attempt}, retrying with stricter prompt"
                    )
                    prompt = prompt + "\n\nIMPORTANT: Output ONLY the JSON object. No text before or after. No markdown."
                else:
                    raise ValueError(
                        f"Ollama returned non-JSON after 2 attempts: {raw_text[:200]}"
                    ) from exc
