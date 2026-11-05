"""FresherPilot reader for GitHub Actions: reads LinkedIn and Indeed while the laptop is off and hands new
jobs to the cloud (FresherPilot Cloud), which checks them with AI and sends the Telegram alerts.

Why here: the cloud (Cloudflare) can't read LinkedIn/Indeed
(rate-limited / blocked). GitHub's runners can (measured Nov 5 2026: LinkedIn 31/32 searches + 20/20 job
pages, Indeed 360/360 jobs in 4 runs on 4 different runner IPs).

Nothing personal is in this repo: the searches and title rules come from the cloud at run time, and the cloud
address + secret are GitHub secrets (FP_CLOUD_URL, FP_CLOUD_SECRET).
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

CLOUD = os.environ["FP_CLOUD_URL"].rstrip("/")
HEAD = {"X-FP-Secret": os.environ["FP_CLOUD_SECRET"]}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept-Language": "en-IN,en;q=0.9"}
LI_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LI_JOB = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
LI_GAP = 2.5          # seconds between LinkedIn requests (the laptop's pace)
OVERLAP = 10 * 60     # read 10 min further back than the last read (posts show up in search a bit late)
MIN_WINDOW = 30 * 60  # like the laptop's fast pass
MAX_DETAILS = 80      # job pages per round (the rest: next round, the window isn't advanced)
# One run loops for almost GitHub's 6 h job limit (a LinkedIn round every ~2-4 min, Indeed every 10 min); the
# cloud starts the next run (it waits in the queue meanwhile: workflow "concurrency"), so reading never stops.
RUN_TOTAL = int(os.getenv("FP_RUN_MINUTES", "340")) * 60
INDEED_EVERY = 10 * 60
MIN_ROUND = 60        # seconds: a round faster than this waits (be polite)
START = time.time()
DEADLINE = START + RUN_TOTAL
# The workflow runs 3 machines at once (separate IPs, so LinkedIn's per-IP limit isn't shared): "li0" and "li1"
# read half of the LinkedIn searches each (a round ~1 min), "indeed" loops Indeed. "all" = everything (one machine).
SHARD = os.getenv("FP_SHARD", "all")


def cloud(path: str, body: dict | None = None) -> dict:
    r = (requests.post(CLOUD + path, json=body, headers=HEAD, timeout=60) if body is not None
         else requests.get(CLOUD + path, headers=HEAD, timeout=60))
    r.raise_for_status()
    return r.json()


def days_ago(dt: datetime | None) -> int | None:
    return None if dt is None else max(0, (datetime.now(timezone.utc) - dt).days)


def window_for(read_at: float | None, laptop_seen_ms: int, cap_days: int) -> tuple[int, float]:
    """Seconds to read back: since the later of the last read and the laptop going quiet, plus overlap."""
    start = max(read_at or 0, laptop_seen_ms / 1000) - OVERLAP
    return int(min(cap_days * 86400, max(MIN_WINDOW, time.time() - start))), start


# ---------------------------------------------------------------- LinkedIn (guest search, like the laptop)

class Blocked(Exception):
    pass


_li_last = 0.0


def li_get(url: str, params: dict | None = None) -> requests.Response | None:
    global _li_last
    for pause in (10, 30, 60):
        wait = _li_last + LI_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _li_last = time.time()
        r = requests.get(url, params=params, headers=UA, timeout=30)
        if r.status_code == 200:
            return r
        if r.status_code in (400, 404):
            return None
        time.sleep(pause)  # 429 / 999 = rate limit
    raise Blocked("LinkedIn rate-limited this runner")


def li_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("div.base-search-card, div.job-search-card"):
        jid = (card.get("data-entity-urn") or "").rsplit(":", 1)[-1]
        title = card.select_one(".base-search-card__title")
        if not jid.isdigit() or not title:
            continue
        company = card.select_one(".base-search-card__subtitle")
        loc = card.select_one(".job-search-card__location")
        posted = card.select_one("time")
        out.append({"id": jid, "title": title.get_text(" ", strip=True),
                    "company": company.get_text(" ", strip=True) if company else "Unknown",
                    "location": loc.get_text(" ", strip=True) if loc else "",
                    "posted": posted.get("datetime", "") if posted else ""})
    return out


def li_detail(card: dict, within_min: int) -> dict:
    d = {"description": "", "level": "", "type": ""}
    r = li_get(LI_JOB.format(card["id"]))
    if r is not None:
        soup = BeautifulSoup(r.text, "html.parser")
        desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
        crit = {}
        for item in soup.select(".description__job-criteria-item"):
            h, v = item.select_one("h3"), item.select_one("span")
            if h and v:
                crit[h.get_text(strip=True).lower()] = v.get_text(strip=True)
        d = {"description": desc.get_text("\n", strip=True) if desc else "",
             "level": crit.get("seniority level", ""), "type": crit.get("employment type", ""),
             # Apply button: onsite = Easy Apply on LinkedIn (mobile app), offsite = the company's site
             "apply": "site" if "apply-link-offsite" in r.text else "easy" if "apply-link-onsite" in r.text
                      or "apply-link-simple_onsite" in r.text else ""}
    try:
        posted_dt = datetime.fromisoformat(card["posted"]).replace(tzinfo=timezone.utc) if card["posted"] else None
    except ValueError:
        posted_dt = None
    return {"id": card["id"], "source": "LinkedIn", "title": card["title"], "company": card["company"],
            "location": (card["location"] if "remote" in card["location"].lower() or not card.get("remote")
                         else f"Remote, {card['location']}" if card["location"] else "Remote"),
            "experience": d["level"] or "Not specified", "posted": card["posted"],
            "posted_days": days_ago(posted_dt), "url": f"https://www.linkedin.com/jobs/view/{card['id']}/",
            "description": d["description"], "work_type": d["type"], "posted_within_min": within_min,
            "apply_mode": d.get("apply", "")}


def read_linkedin(cfg: dict, known: set[str]) -> tuple[list[dict], bool]:
    """All searches over the window. Returns (jobs, complete)."""
    read = cfg["read"] or {}
    # a new machine split starts where the last LinkedIn read (any split) ended, not 3 days back
    last = read.get(li_key()) or max([v for k, v in read.items() if k.startswith("linkedin")] or [0]) or None
    window, _ = window_for(last, cfg["laptop_seen"], cfg["posted_within_days"])
    pages = 1 if window <= 3600 else min(40, 2 + window // 3600)  # longer gap: read deeper
    new_cards: dict[str, dict] = {}
    searches = cfg["li_searches"]
    if SHARD in ("li0", "li1"):
        searches = searches[int(SHARD[-1])::2]
    for title, place in searches:
        if time.time() > DEADLINE:
            return finish_li(new_cards, window), False
        params = {"keywords": title, "location": "India" if place == "Remote" else place, "f_TPR": f"r{window}",
                  "f_JT": "F", "sortBy": "DD"}
        if place == "Remote":
            params["f_WT"] = "2"
        for page in range(int(pages)):
            r = li_get(LI_SEARCH, {**params, "start": page * 10})
            found = li_cards(r.text) if r is not None else []
            fresh = [c for c in found if f"LinkedIn:{c['id']}" not in known and c["id"] not in new_cards]
            for c in fresh:
                c["remote"] = place == "Remote"  # LinkedIn keeps the office city in the location: mark it
                new_cards[c["id"]] = c
            if len(found) < 10 or not fresh:
                break  # last page, or only known jobs: the rest is older
    return finish_li(new_cards, window), True


def li_key() -> str:
    return "linkedin" if SHARD == "all" else f"linkedin_{SHARD}"


def finish_li(cards: dict[str, dict], window: int) -> list[dict]:
    if not cards:
        return []
    items = list(cards.values())
    keep = cloud("/api/titles", {"titles": [c["title"] for c in items]})["keep"]
    passing = [c for c, k in zip(items, keep) if k]
    print(f"LinkedIn: {len(items)} new cards, {len(passing)} pass the title rules")
    jobs = []
    for c in passing[:MAX_DETAILS]:
        jobs.append(li_detail(c, round(window / 60)))
    return jobs


# ---------------------------------------------------------------- Indeed (python-jobspy, like the laptop)

def read_indeed(cfg: dict, known: set[str]) -> tuple[list[dict], bool]:
    from jobspy import scrape_jobs

    window, _ = window_for((cfg["read"] or {}).get("indeed"), cfg["laptop_seen"], cfg["posted_within_days"])
    hours = max(1, int(window / 3600 + 0.999))
    jobs: dict[str, dict] = {}
    errors = 0
    for role in cfg["indeed_roles"]:
        for place in cfg["places"]:
            if time.time() > DEADLINE:
                return list(jobs.values()), False
            try:
                df = scrape_jobs(site_name=["indeed"], search_term=role, country_indeed="India",
                                 location="India" if place == "Remote" else place, is_remote=place == "Remote",
                                 hours_old=hours, results_wanted=100, description_format="markdown", verbose=0)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print("Indeed error:", str(exc)[:150])
                continue
            for row in df.to_dict("records"):
                jid = str(row.get("id") or "").removeprefix("in-")
                if not jid or jid in jobs or f"Indeed:{jid}" in known or not row.get("title"):
                    continue
                p = row.get("date_posted")
                pdt = datetime(p.year, p.month, p.day, tzinfo=timezone.utc) if hasattr(p, "year") else None
                text = lambda v: "" if v is None or v != v else str(v)  # noqa: E731 (NaN-safe)
                loc = text(row.get("location"))
                if place == "Remote" or row.get("is_remote"):
                    loc = loc if "remote" in loc.lower() else f"Remote, {loc}"
                elif place.lower() not in loc.lower():
                    loc = f"{place}, {loc}" if loc else place
                jobs[jid] = {"id": jid, "source": "Indeed", "title": str(row["title"]),
                             "company": text(row.get("company")) or "Unknown", "location": loc,
                             "experience": text(row.get("experience_range")) or "Not specified",
                             "posted": pdt.date().isoformat() if pdt else "", "posted_days": days_ago(pdt),
                             "url": text(row.get("job_url")), "description": re.sub(r"<[^>]+>", " ", text(row.get("description"))),
                             "work_type": text(row.get("job_type")),
                             # a company link = apply on the company's site; none = Indeed Apply (mobile app)
                             "apply_mode": "site" if text(row.get("job_url_direct")).strip() else "easy",
                             "posted_within_min": round(hours * 60) if hours <= 6 else None}
            time.sleep(1.0)
    items = list(jobs.values())
    if items:
        keep = cloud("/api/titles", {"titles": [j["title"] for j in items]})["keep"]
        items = [j for j, k in zip(items, keep) if k]
    return items, errors < len(cfg["indeed_roles"]) * len(cfg["places"])


def main() -> int:
    force = "?force=1" if os.getenv("FP_FORCE") == "1" else ""
    do_li, do_indeed = SHARD in ("all", "li0", "li1"), SHARD in ("all", "indeed")
    indeed_every = 0 if SHARD == "indeed" else INDEED_EVERY  # its own machine: back to back
    last_indeed = 0.0
    rounds = 0
    while time.time() < DEADLINE:
        began = time.time()
        cfg = cloud("/api/reader" + force)  # fresh each round: settings, what's known, how far each board was read
        if not cfg["quiet"]:
            print("An always-on laptop app is reading the job sites: waiting.")
            time.sleep(300)
            continue
        known = set(cfg["known"])
        line = f"round {rounds + 1} [{SHARD}]:"
        if do_li:
            read: dict[str, float] = {}
            try:
                li, done = read_linkedin(cfg, known)
                if done:
                    read[li_key()] = began
            except Blocked as exc:
                li = []
                print(exc)
                time.sleep(120)
            res = cloud("/api/ingest", {"jobs": li, "read": read})
            line += f" LinkedIn sent {len(li)} (new {res['added']})"
        if do_indeed and began - last_indeed >= indeed_every:
            last_indeed = began
            ind, done = read_indeed(cfg, known)
            res = cloud("/api/ingest", {"jobs": ind, "read": {"indeed": began} if done else {}})
            line += f" Indeed sent {len(ind)} (new {res['added']})"
        rounds += 1
        print(f"{datetime.now(timezone.utc):%H:%M:%S} {line}, {time.time() - began:.0f}s", flush=True)
        time.sleep(max(0.0, MIN_ROUND - (time.time() - began)))
    print(f"Done: {rounds} rounds in {(time.time() - START) / 60:.0f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
