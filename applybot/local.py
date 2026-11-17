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

PORTALS = ["https://www.linkedin.com/login", "https://www.naukri.com/nlogin/login", "https://secure.indeed.com/auth", "https://internshala.com/login", "https://wellfound.com/login"]


def load_env(root: Path) -> None:
    from dotenv import dotenv_values
    for k, v in dotenv_values(root / ".env").items():
        if v and k in ("FP_CLOUD_URL", "FP_CLOUD_SECRET", "APPLY_PW_SECRET", "GMAIL_APP_PASSWORD"):
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
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(prof_dir, channel="chrome", headless=False, viewport=None, args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
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
        resume_path = ""
        print("local apply runner: waiting for jobs (Ctrl+C to stop)", flush=True)
        while True:
            try:
                claim = cloud._call("GET", "/api/apply/claim?scope=local")
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
                try:
                    status, detail = run.run()
                finally:
                    steps = "\n".join(run.steps)
                    for pg in list(ctx.pages)[1:]:   # leave one tab open, close the rest
                        try:
                            pg.close()
                        except Exception:  # noqa: BLE001
                            pass
                print(f"   -> {status}: {detail}", flush=True)
                cloud.result(job["id"], status, detail, steps)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                try:
                    cloud.result(job["id"], "failed", f"Runner error: {str(e)[:200]}", steps)
                except Exception:  # noqa: BLE001
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
