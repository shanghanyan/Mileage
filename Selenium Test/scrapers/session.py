"""Fresh browser profiles and post-run wipe."""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
from pathlib import Path

from selenium.webdriver.remote.webdriver import WebDriver


class HostProfileError(RuntimeError):
    pass


def require_remote_scrape() -> None:
    """Refuse to start a browser unless REMOTE_SCRAPE=1 (isolated VPS context)."""
    if os.environ.get("REMOTE_SCRAPE") != "1":
        raise HostProfileError(
            "REMOTE_SCRAPE=1 is required. Browser scrapes must run on the Ubuntu "
            "VPS (or an isolated Linux box), never against a laptop Chrome/Safari/Firefox profile."
        )


def make_temp_profile(prefix: str = "scrape-profile-") -> Path:
    require_remote_scrape()
    return Path(tempfile.mkdtemp(prefix=prefix))


def wipe_driver(driver: WebDriver | None) -> None:
    if driver is None:
        return
    try:
        driver.delete_all_cookies()
    except Exception:
        pass
    try:
        driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear();")
    except Exception:
        pass
    try:
        driver.quit()
    except Exception:
        pass


def wipe_profile(profile_dir: Path | None) -> None:
    if profile_dir and profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    gc.collect()
