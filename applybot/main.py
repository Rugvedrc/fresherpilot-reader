"""GitHub runner entry: take jobs from the cloud queue until it is empty, apply to each in a headless browser, report every result.

env: FP_CLOUD_URL, FP_CLOUD_SECRET, APPLY_PW_SECRET (portal passwords are derived from it), DRY_RUN=1 (fill everything, never press the final Submit)
"""
from __future__ import annotations

import base64
import html
import os
import re
import sys
import tempfile
import time
import traceback
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

from . import cloud
from .engine import Run

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
# sites that need the owner's own login (his laptop's Chrome does these) or that block cloud machines
LAPTOP_ONLY = re.compile(r"(^|\.)(naukri\.com|indeed\.com|internshala\.com|wellfound\.com|foundit\.in|glassdoor\.\w+|shine\.com|apna\.co)$", re.I)
TOTAL_MINUTES = 50


def resolve_linkedin(url: str) -> str | None:
    """A LinkedIn job page lists the company's own apply link (for jobs that are not Easy Apply) in its public HTML."""
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}, timeout=25)
        m = re.search(r'id="applyUrl"[^>]*>\s*<!--"?([^"<]+)"?-->', r.text) or re.search(r'"applyUrl"\s*:\s*"([^"]+)"', r.text)
        if m:
            link = html.unescape(m.group(1)).replace("\\u0026", "&").replace("\\/", "/")
            q = re.search(r"[?&]url=([^&]+)", link)
            if q:
                from urllib.parse import unquote
                link = unquote(q.group(1))
            if link.startswith("http") and "linkedin.com" not in urlparse(link).netloc:
                return link
    except Exception:  # noqa: BLE001
        pass
    return None


def prepare(job: dict) -> tuple[str | None, str | None, str | None]:
    """(url to open, early status, detail)."""
    url = job["url"]
    host = (urlparse(url).hostname or "").removeprefix("www.")
    if "linkedin.com" in host:
        ext = resolve_linkedin(url)
        if ext:
            return ext, None, None
        return None, "needs_you", "LinkedIn Easy Apply needs your own LinkedIn login: use the app / extension for this one"
    if LAPTOP_ONLY.search(host):
        return None, "needs_you", f"{host} needs your logged-in Chrome (laptop), the cloud machine can't use it"
    return url, None, None


def main() -> int:
    dry = os.environ.get("DRY_RUN", "") == "1"
    print(f"apply runner started (dry run: {dry})", flush=True)
    resume_path = ""
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        while time.time() - started < TOTAL_MINUTES * 60:
            claim = cloud.claim()
            job = claim.get("job")
            if not job:
                break
            profile = claim.get("profile")
            print(f"job {job['id']}: {job['title'][:50]} @ {job['company'][:30]}", flush=True)
            steps = ""
            try:
                if not profile:
                    cloud.result(job["id"], "failed", "No profile uploaded to the cloud yet", "")
                    continue
                if not resume_path:
                    f = cloud.file("resume")
                    resume_path = os.path.join(tempfile.mkdtemp(), "Resume.pdf")
                    open(resume_path, "wb").write(base64.b64decode(f["body"]))
                url, early, detail = prepare(job)
                if early:
                    cloud.result(job["id"], early, detail or "", "")
                    continue
                job = dict(job, url=url)
                ctx = browser.new_context(viewport={"width": 1366, "height": 850}, user_agent=UA, locale="en-IN", timezone_id="Asia/Kolkata", accept_downloads=False)
                ctx.set_default_timeout(15000)
                run = Run(ctx, job, profile, claim.get("accounts") or [], resume_path, dry)
                try:
                    status, detail = run.run()
                finally:
                    steps = "\n".join(run.steps)
                    ctx.close()
                print(f"   -> {status}: {detail}", flush=True)
                cloud.result(job["id"], status, detail, steps)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                try:
                    cloud.result(job["id"], "failed", f"Runner error: {str(e)[:200]}", steps)
                except Exception:  # noqa: BLE001
                    pass
        browser.close()
    print("apply runner done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
