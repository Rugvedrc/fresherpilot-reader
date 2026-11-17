"""Feasibility probe: can a headless browser on a GitHub runner open the career/apply pages? Read-only: no login, no form, no submit."""
import json, sys, time
from playwright.sync_api import sync_playwright

URLS = {
  "IBM job": "https://careers.ibm.com/en_US/careers/JobDetail?jobId=139157",
  "IBM search": "https://www.ibm.com/careers/search?field_keyword_18[0]=Entry%20Level&field_keyword_05[0]=India",
  "Cognizant": "https://careers.cognizant.com/india-en/jobs/",
  "NTT DATA": "https://nttdata.wd1.myworkdayjobs.com/",
  "Capgemini": "https://www.capgemini.com/in-en/careers/join-capgemini/job-search/",
  "Wipro": "https://careers.wipro.com/careers-home/jobs",
  "Accenture": "https://www.accenture.com/in-en/careers/jobsearch",
  "Infosys": "https://career.infosys.com/",
  "Workday sample (Cisco)": "https://cisco.wd5.myworkdayjobs.com/en-US/Cisco_Careers",
  "LinkedIn job": "https://www.linkedin.com/jobs/view/4486866178/",
  "Naukri": "https://www.naukri.com/python-developer-jobs-in-pune",
  "Indeed": "https://in.indeed.com/jobs?q=python+fresher&l=Pune",
}
MARK = ["captcha", "verify you are human", "access denied", "are you a robot", "unusual traffic", "blocked", "cloudflare", "just a moment",
        "pardon our interruption", "request unsuccessful", "perimeterx", "datadome", "press & hold"]

def run(p, name, args):
    b = p.chromium.launch(headless=True, args=args)
    ctx = b.new_context(viewport={"width": 1366, "height": 800}, locale="en-IN", timezone_id="Asia/Kolkata",
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
    out = []
    for label, url in URLS.items():
        pg = ctx.new_page(); t = time.time(); status = None; err = ""
        try:
            r = pg.goto(url, wait_until="domcontentloaded", timeout=40000)
            status = r.status if r else None
            pg.wait_for_timeout(5000)
            text = (pg.inner_text("body") or "")[:3000]
        except Exception as e:  # noqa: BLE001
            text, err = "", str(e)[:120]
        low = text.lower()
        hits = [m for m in MARK if m in low]
        buttons = 0
        try: buttons = pg.locator("button, a[role=button], input[type=submit]").count()
        except Exception: pass
        out.append({"variant": name, "site": label, "status": status, "title": (pg.title() or "")[:70], "chars": len(text), "buttons": buttons,
                    "block_words": hits, "secs": round(time.time() - t, 1), "err": err})
        pg.close()
    b.close()
    return out

with sync_playwright() as p:
    res = run(p, "default", []) + run(p, "no-automation-flag", ["--disable-blink-features=AutomationControlled"])
print(json.dumps(res, indent=1))
for r in res:
    ok = r["status"] and r["status"] < 400 and r["chars"] > 300 and not r["block_words"]
    print(("OK   " if ok else "FAIL ") + f"{r['variant']:18} {r['site']:24} status={r['status']} chars={r['chars']} btn={r['buttons']} block={r['block_words']} {r['err']}")
