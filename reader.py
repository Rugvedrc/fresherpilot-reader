"""FresherPilot reader for GitHub Actions: reads LinkedIn and Indeed while the laptop is off and hands new
jobs to the cloud (FresherPilot Cloud), which checks them with AI and sends the Telegram alerts.

Why here: the laptop reads from a home IP; when it's off, the cloud (Cloudflare) can't read LinkedIn/Indeed
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
MAX_DETAILS = 80      # job pages per run (the rest: next run, the window isn't advanced)
RUN_LIMIT = 11 * 60   # stop reading after this many seconds (the workflow runs every 10 min)
START = time.time()


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
             "level": crit.get("seniority level", ""), "type": crit.get("employment type", "")}
    try:
        posted_dt = datetime.fromisoformat(card["posted"]).replace(tzinfo=timezone.utc) if card["posted"] else None
    except ValueError:
        posted_dt = None
    return {"id": card["id"], "source": "LinkedIn", "title": card["title"], "company": card["company"],
            "location": card["location"], "experience": d["level"] or "Not specified", "posted": card["posted"],
            "posted_days": days_ago(posted_dt), "url": f"https://www.linkedin.com/jobs/view/{card['id']}/",
            "description": d["description"], "work_type": d["type"], "posted_within_min": within_min}


def read_linkedin(cfg: dict, known: set[str]) -> tuple[list[dict], bool]:
    """All searches over the window. Returns (jobs, complete)."""
    window, _ = window_for((cfg["read"] or {}).get("linkedin"), cfg["laptop_seen"], cfg["posted_within_days"])
    pages = 1 if window <= 3600 else min(40, 2 + window // 3600)  # longer gap: read deeper
    new_cards: dict[str, dict] = {}
    for title, place in cfg["li_searches"]:
        if time.time() - START > RUN_LIMIT * 0.6:
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
                new_cards[c["id"]] = c
            if len(found) < 10 or not fresh:
                break  # last page, or only known jobs: the rest is older
    return finish_li(new_cards, window), True


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
            if time.time() - START > RUN_LIMIT:
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
                             "posted_within_min": round(hours * 60) if hours <= 6 else None}
            time.sleep(1.0)
    items = list(jobs.values())
    if items:
        keep = cloud("/api/titles", {"titles": [j["title"] for j in items]})["keep"]
        items = [j for j, k in zip(items, keep) if k]
    return items, errors < len(cfg["indeed_roles"]) * len(cfg["places"])


def main() -> int:
    cfg = cloud("/api/reader" + ("?force=1" if os.getenv("FP_FORCE") == "1" else ""))
    if not cfg["quiet"]:
        print("Laptop app is online and reading the job sites: nothing to do.")
        return 0
    known = set(cfg["known"])
    started = time.time()
    print(f"Laptop quiet since {datetime.fromtimestamp(cfg['laptop_seen'] / 1000, timezone.utc):%Y-%m-%d %H:%M} UTC; "
          f"{len(known)} known jobs")
    read: dict[str, float] = {}
    try:
        li, done = read_linkedin(cfg, known)
        if done:
            read["linkedin"] = started
    except Blocked as exc:
        li = []
        print(exc)
    res = cloud("/api/ingest", {"jobs": li, "read": read})
    print(f"LinkedIn: sent {len(li)}, new to the cloud {res['added']}")
    ind, done = read_indeed(cfg, known)
    res = cloud("/api/ingest", {"jobs": ind, "read": {"indeed": started} if done else {}})
    print(f"Indeed: sent {len(ind)}, new to the cloud {res['added']}")
    print(f"Done in {time.time() - START:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
