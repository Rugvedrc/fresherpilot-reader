"""Local runner: applies on the portals that need YOUR logged-in browser (LinkedIn Easy Apply, Naukri, Indeed, Internshala, Wellfound).

It drives a real Chrome window with its own profile folder (sign in to each portal once with --login; the sessions stay),
takes jobs of scope "local" from the cloud queue, and uses the same engine as the cloud runner (real mouse / keyboard, page-state reader,
answer bank, Gmail code reader). Dry mode (the cloud's apply_dry switch, /applylive) fills everything but stops before the final Submit.

  python -m scripts.apply_local --login     # opens Chrome on the portals: sign in once, then close the window
  python -m scripts.apply_local             # works through the queue, keeps polling
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

# Gentle pace = the best protection for the owner's accounts: a daily cap per portal and a random pause between applications.
DAILY_CAP = {"LinkedIn": 0, "Naukri": 40, "Indeed": 30, "Internshala": 30, "Wellfound": 20, "Foundit": 30}
PAUSE = (35, 95)   # seconds between two applications

PORTALS = ["https://www.linkedin.com/login", "https://www.naukri.com/nlogin/login", "https://secure.indeed.com/auth", "https://internshala.com/login", "https://wellfound.com/login"]


def load_env(root: Path) -> None:
    from dotenv import dotenv_values
    for k, v in dotenv_values(root / ".env").items():
        if v and k in ("FP_CLOUD_URL", "FP_CLOUD_SECRET", "APPLY_PW_SECRET", "GMAIL_APP_PASSWORD", "GOOGLE_PASSWORD"):
            os.environ.setdefault(k, v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="open the portals to sign in once")
    ap.add_argument("--profile", default="", help="Chrome profile folder (default: users/<owner>/applybot_profile)")
    ap.add_argument("--once", action="store_true", help="stop when the queue is empty")
    ap.add_argument("--dry", action="store_true", help="never press the final Submit, whatever the cloud switch says")
    a = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    load_env(root)
    sys.path.insert(0, str(root / "github_reader"))
    import json
    from playwright.sync_api import sync_playwright
    from applybot import cloud
    from applybot.engine import Run

    prof_dir = a.profile or str(root / "users" / next(p.name for p in (root / "users").iterdir() if (p / "applicant_profile.json").exists()) / "applybot_profile")
    Path(prof_dir).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("GMAIL_USER", "")
    if a.login:
        # Sign in in a PLAIN Chrome (no automation flags): Google / LinkedIn refuse sign-in in a browser an automation tool controls.
        # The same profile folder is then used by the runner, so the sessions carry over.
        import glob
        import subprocess
        chrome = next((c for c in (os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"), os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                                   os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")) if os.path.exists(c)), "")
        if not chrome:
            print("Chrome not found")
            return 1
        print("Opening a normal Chrome window. Sign in to each tab (LinkedIn: use Google if you like), then close the window. Profile:", prof_dir)
        subprocess.run([chrome, f"--user-data-dir={prof_dir}", "--no-first-run", "--no-default-browser-check", *PORTALS])
        return 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(prof_dir, channel="chrome", headless=(os.environ.get("APPLYBOT_HEADLESS", "1") == "1"), viewport=None, args=["--start-maximized", "--disable-blink-features=AutomationControlled"], ignore_default_args=["--enable-automation"],
                                                   locale="en-IN", timezone_id="Asia/Kolkata")
        if a.login:
            for u in PORTALS:
                pg = ctx.new_page()
                pg.goto(u)
            print("Sign in to each portal in the opened tabs, then close the Chrome window. The sessions are kept in:", prof_dir)
            try:
                while ctx.pages:
                    time.sleep(2)
            except Exception:  # noqa: BLE001
                pass
            return 0
        import random
        done_today: dict[str, int] = {}
        kept_tabs: list = []                 # CAPTCHA-blocked applications left open for the owner
        handoff: list[str] = []              # Easy Apply jobs for the extension system (the owner's normal Chrome)
        last_handoff = 0.0
        blocked_run: dict[str, int] = {}     # consecutive CAPTCHA blocks per portal
        paused_until: dict[str, float] = {}  # portal -> time it may be tried again
        day = time.strftime("%Y-%m-%d")
        resume_path = ""
        from applybot import mailwatch
        try:   # GMAIL_USER defaults to the profile email; the app password comes from .env
            if not os.environ.get("GMAIL_USER"):
                import json as _j
                os.environ["GMAIL_USER"] = _j.loads(cloud.file("profile")["body"])["personal"]["email"]
        except Exception:  # noqa: BLE001
            pass
        if mailwatch.start():
            print("Gmail watcher on: codes and verification links are forwarded to Telegram", flush=True)
        print("local apply runner: waiting for jobs (Ctrl+C to stop)", flush=True)
        while True:
            try:
                if handoff and time.time() - last_handoff > 45:   # the app (old system) applies to these through the extension in the normal Chrome
                    last_handoff = time.time()
                    try:
                        import requests as _rq
                        r = _rq.post("http://localhost:8000/api/autoapply", json={"pick": [u for u in handoff if u], "max": len(handoff)}, timeout=20)
                        print(f"handoff of {len(handoff)} job(s) to the extension system: HTTP {r.status_code}", flush=True)
                        if r.status_code == 200:
                            handoff.clear()
                    except Exception as e:  # noqa: BLE001
                        print("handoff failed (app not running?):", str(e)[:80], flush=True)
                if time.strftime("%Y-%m-%d") != day:
                    day, done_today = time.strftime("%Y-%m-%d"), {}
                capped = ",".join(k for k in DAILY_CAP if done_today.get(k, 0) >= DAILY_CAP[k] or paused_until.get(k, 0) > time.time())   # cap 0 (LinkedIn) = never claimed
                claim = cloud._call("GET", "/api/apply/claim?scope=local" + (f"&skip={capped}" if capped else ""))
            except Exception as e:  # noqa: BLE001
                print("cloud not reachable:", str(e)[:80])
                time.sleep(30)
                continue
            job = claim.get("job")
            if not job:
                if a.once:
                    break
                time.sleep(15)
                continue
            if str(job.get("source", "")).lower() == "linkedin" or "linkedin.com" in str(job.get("url", "")):
                cloud.result(job["id"], "skipped", "LinkedIn jobs are never auto-applied (protects your account): apply by hand", "")
                continue
            from urllib.parse import urlparse as _up
            from applybot.engine import PORTAL as _PORTAL
            if _PORTAL.search((_up(job.get("url", "")).hostname or "").removeprefix("www.")):
                # a job-portal link (Indeed / Naukri / Internshala / Wellfound): the old extension system does these in the owner's own Chrome.
                # (A separate automation browser is challenged by those portals right away.) Playwright only takes company career-site links.
                handoff.append(job.get("uid") or "")
                cloud.result(job["id"], "skipped", "Easy Apply: handed to the extension system in your normal Chrome", "")
                print(f"job {job['id']}: portal link, handed to the extension system", flush=True)
                continue
            profile = claim.get("profile")
            dry = a.dry or bool(cloud._call("GET", "/api/apply/dry").get("dry", True))
            print(f"job {job['id']}: {job['title'][:50]} @ {job['company'][:30]} ({job['source']}) dry={dry}", flush=True)
            steps = ""
            try:
                if not profile:
                    cloud.result(job["id"], "failed", "No profile uploaded to the cloud yet", "")
                    continue
                if not resume_path:
                    f = cloud.file("resume")
                    resume_path = os.path.join(tempfile.mkdtemp(), "Resume.pdf")
                    open(resume_path, "wb").write(base64.b64decode(f["body"]))
                if not os.environ.get("GMAIL_USER"):
                    os.environ["GMAIL_USER"] = profile["personal"]["email"]
                run = Run(ctx, job, profile, claim.get("accounts") or [], resume_path, dry)
                status = "failed"
                try:
                    status, detail = run.run()
                finally:
                    steps = "\n".join(run.steps)
                    if status == "blocked" and os.environ.get("APPLYBOT_HEADLESS", "1") != "1" and len(kept_tabs) < 4:   # filled in and waiting for the owner's CAPTCHA tick + Submit: leave it open
                        kept_tabs.append(run.page)
                        detail = "Everything is filled in. In the auto-apply Chrome window, open the tab for this job, tick the CAPTCHA box and press Submit."
                    for pg in list(ctx.pages)[1:]:   # leave one tab open (+ the kept ones), close the rest
                        if any(pg is k for k in kept_tabs):
                            continue
                        try:
                            pg.close()
                        except Exception:  # noqa: BLE001
                            pass
                if status == "handoff":
                    handoff.append(job.get("uid") or "")
                    status, detail = "skipped", "Easy Apply: handed to the extension system in your normal Chrome"
                print(f"   -> {status}: {detail}", flush=True)
                cloud.result(job["id"], status, detail, steps)
                if status == "applied":
                    done_today[job["source"]] = done_today.get(job["source"], 0) + 1
                    blocked_run[job["source"]] = 0
                elif status == "blocked":
                    blocked_run[job["source"]] = blocked_run.get(job["source"], 0) + 1
                    if False:   # (pausing replaced by leaving the filled application open)
                        paused_until[job["source"]] = time.time() + 6 * 3600
                        blocked_run[job["source"]] = 0
                        print(f"{job['source']}: CAPTCHA twice in a row, paused for 6 hours", flush=True)
                        try:
                            cloud._call("POST", "/api/apply/notify", json={"text": f"🛑 <b>{job['source']}</b> keeps showing a CAPTCHA to the auto-apply browser, so I paused it for 6 hours. Those jobs need you (tick the box) or the Easy Apply route."})
                        except Exception:  # noqa: BLE001
                            pass
                time.sleep(random.uniform(*PAUSE))
            except Exception as e:  # noqa: BLE001
                if "closed" in str(e).lower() or "Target page" in str(e):   # the Chrome window was closed: put the job back and stop (no error spam)
                    try:
                        cloud._call("POST", "/api/apply/requeue", json={"id": job["id"]})
                    except Exception:  # noqa: BLE001
                        pass
                    print("Chrome window closed: stopping (jobs stay queued)", flush=True)
                    return 2
                traceback.print_exc()
                try:
                    cloud.result(job["id"], "failed", f"Runner error: {str(e)[:200]}", steps)
                except Exception:  # noqa: BLE001
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
