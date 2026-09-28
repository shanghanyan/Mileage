# Travel Intelligence Scraper (pilot)

**Current scope:** United Airlines, United credit cards (on united.com), Capital One credit cards only.  
**Time limit:** Stops automatically after **1 hour** and writes partial `output.csv`.

---

## Terminal: start to finish

Copy and run each block in **Terminal** (`Terminal.app` or the Cursor integrated terminal).

### 1. Go to the scraper folder

```bash
cd "/Users/avacheng/Code Projects/Pointy/scraper"
```

### 2. One-time setup (Python, Playwright, folders)

```bash
chmod +x setup.sh
./setup.sh
```

This creates `.venv`, installs packages, installs Chromium, and checks Ollama/LLaVA.

### 3. Screenshot & data folders (required)

Playwright saves PNGs here for LLaVA analysis (kept in `screenshots/` after each page). Create both folders once:

```bash
mkdir -p screenshots data
```

Optional — pin absolute paths in `scraper.py` (otherwise defaults above are used):

```python
SCREENSHOTS_DIR = "/Users/avacheng/Code Projects/Pointy/scraper/screenshots"
DATA_DIR = "/Users/avacheng/Code Projects/Pointy/scraper/data"
```

**Visible browser:** In `config.yaml`, set `crawl.headless: false` to open a real Chromium window on your screen (smooth scroll through each page). Set `headless: true` for background runs.

**macOS permissions:** Page screenshots are captured inside Chromium. You usually do **not** need Screen Recording for Terminal. If Playwright fails with a permissions error, grant **Full Disk Access** to Terminal (or Cursor) under **System Settings → Privacy & Security**.

### 4. Start Ollama + LLaVA (vision fallback; optional but recommended)

In a **second terminal tab** (leave it running):

```bash
ollama serve
```

In the **first tab** (only needed once per machine):

```bash
ollama pull llava
```

Verify:

```bash
curl -s http://localhost:11434/api/tags | head
```

If Ollama is off, the scraper still runs in **DOM-only** mode.

### 5. Activate the virtual environment

Every new terminal session before running the scraper:

```bash
cd "/Users/avacheng/Code Projects/Pointy/scraper"
source .venv/bin/activate
```

### 6. Run the crawl (≤ 1 hour)

```bash
python scraper.py
```

Watch logs in the terminal and in `data/scraper_YYYY-MM-DD.log`.

Stop early anytime: `Ctrl+C` — keeps `data/checkpoint.json` and still exports CSV on exit.

### 7. After the run — outputs

| File | Purpose |
|------|---------|
| `data/output.csv` | Main result for your algorithm |
| `data/united_scraper.sqlite` | Raw structured storage |
| `data/visit_log.jsonl` | One JSON line per page |
| `data/failed_urls.log` | Pages where DOM and vision both failed |
| `data/skipped_urls.log` | Out-of-scope / blocklisted URLs |
| `screenshots/page_*.png` | Full-page captures sent to LLaVA (retained for review) |

Quick check:

```bash
wc -l data/output.csv
head -3 data/output.csv
```

### 8. Resume after timeout or Ctrl+C

```bash
cd "/Users/avacheng/Code Projects/Pointy/scraper"
source .venv/bin/activate
python scraper.py
```

Checkpoint is removed only when the queue finishes empty before the hour limit.

### 9. Fresh start (optional)

```bash
rm -f data/checkpoint.json data/united_scraper.sqlite data/output.csv
rm -f data/visit_log.jsonl data/failed_urls.log data/skipped_urls.log
rm -f screenshots/*.png
python scraper.py
```

---

## Changing scope or time limit

Edit `config.yaml`:

- `seeds` — starting URLs  
- `scope` — domains, depth, page caps; `path_prefix` on Capital One limits to `/credit-cards`  
- `crawl.max_runtime_seconds` — default `3600` (1 hour)

---

## Scheduled re-crawl

Uncomment the APScheduler block at the bottom of `scraper.py`.
