"""Company career sites read directly (no LinkedIn in between), for the reader machine "careers".

Why (Nov 14 2026 research): the FreshersHunt blog, which publishes drives hours before anyone else, takes its jobs from the
companies' own career systems. Four systems cover most of them and all have open JSON endpoints:
  workday   POST https://<tenant>.<wdN>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs
  eightfold GET  https://<host>/api/pcsx/search?domain=<domain>&location=India&sort_by=timestamp   (+ /position_details)
  phenom    POST https://<host>/widgets   (ddoKey refineSearch, country India, newest first)
  oracle    GET  https://<host>/hcmRestApi/resources/latest/recruitingCEJobRequisitions (Oracle HCM, keyword India)
  amazon    GET  https://www.amazon.jobs/en/search.json
Each lister returns cards of the jobs posted since `since` (a UTC datetime), India only, newest first; detail() adds the
description. Cards: id (without the "Careers:" prefix), title, company, location, posted (ISO), url, plus what detail() needs.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
JSON = {"User-Agent": UA, "Accept": "application/json"}
TIMEOUT = 30
INDIA = re.compile(r"india|bengaluru|bangalore|hyderabad|pune|mumbai|chennai|gurgaon|gurugram|noida|delhi|kolkata|ahmedabad|"
                   r"jaipur|kochi|coimbatore|indore|chandigarh|trivandrum|thiruvananthapuram|nagpur|vadodara|bhubaneswar|"
                   r"mysuru|mysore|visakhapatnam|lucknow|mohali|navi mumbai|thane|remote", re.I)

# (kind, config, company). Verified live Nov 14 2026 unless marked "to verify" (a tenant under Workday maintenance that day).
COMPANIES: list[tuple[str, str, str]] = [
    ("amazon", "", "Amazon"),
    ("cognizant", "", "Cognizant"),
    ("wipro", "careers.wipro.com", "Wipro"),
    ("sf_html", "careers.capgemini.com", "Capgemini"),
    ("oracle", "fa-etvl-saasfaprod1.fa.ocs.oraclecloud.com|CX_1", "Zensar"),
    ("workday", "accenture|AccentureCareers|wd103", "Accenture"),
    ("workday", "genpact|External_Careers|wd108", "Genpact"),                 # to verify (maintenance on Nov 14)
    ("workday", "kyndryl|KyndrylEarlyCareers|wd5", "Kyndryl"),                # to verify: the fresher site
    ("workday", "kyndryl|KyndrylProfessionalCareers|wd5", "Kyndryl"),         # to verify
    # Eightfold: "host|domain"
    ("eightfold", "apply.careers.microsoft.com|microsoft.com", "Microsoft"),
    ("eightfold", "careers.qualcomm.com|qualcomm.com", "Qualcomm"),
    ("eightfold", "paypal.eightfold.ai|paypal.com", "PayPal"),
    ("eightfold", "eaton.eightfold.ai|eaton.com", "Eaton"),
    ("eightfold", "careers.micron.com|micron.com", "Micron"),
    # Phenom: "host|lang|country-path"
    ("phenom", "careers.cisco.com|en_global|global", "Cisco"),
    ("phenom", "careers.hpe.com|en_us|us", "HPE"),
    ("phenom", "careers.gehealthcare.com|en_global|global", "GE Healthcare"),
    ("phenom", "www.pgcareers.com|en_global|global", "P&G"),
    ("phenom", "careers.quest-global.com|en_global|global", "Quest Global"),
    ("phenom", "careers.nttdata.com|en_global|global", "NTT DATA"),
    # Oracle HCM: "host|siteNumber"
    ("oracle", "ibqbjb.fa.ocs.oraclecloud.com|CX_1", "Honeywell"),
    ("oracle", "icfcjb.fa.ocs.oraclecloud.com|CX_1", "Honeywell Aerospace"),
    ("oracle", "jpmc.fa.oraclecloud.com|CX_1001", "JPMorgan Chase"),
    ("oracle", "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com|CX_1", "Nokia"),
    # Workday: "tenant|site|wdN"
    ("workday", "nttlimited|NTT_Careers|wd3", "NTT DATA"),
    ("workday", "walmart|WalmartExternal|wd504", "Walmart"),
    ("workday", "salesforce|External_Career_Site|wd12", "Salesforce"),
    ("workday", "lseg|Careers|wd3", "LSEG"),
    ("workday", "zebra|Zebra_careers|wd501", "Zebra"),
    ("workday", "kaleris|kaleris_careers|wd501", "Kaleris"),
    ("workday", "harriscomputer|1|wd3", "Altera Digital Health"),
    ("workday", "hpe|Jobsathpe|wd5", "HPE"),                       # to verify (maintenance on Nov 14)
    ("workday", "qualys|Careers|wd5", "Qualys"),                    # to verify
    ("workday", "statestreet|Global|wd1", "State Street"),          # to verify
    ("workday", "transunion|TransUnion|wd5", "TransUnion"),         # to verify
    ("workday", "unisys|External|wd5", "Unisys"),                   # to verify
    ("workday", "cigna|cignacareers|wd5", "Cigna"),                 # to verify
    ("workday", "danaher|DanaherJobs|wd1", "Danaher"),              # to verify
    ("workday", "rackspace|External|wd1", "Rackspace"),             # to verify
    ("workday", "silabs|SiliconLabsCareers|wd1", "Silicon Labs"),   # to verify
    ("workday", "edwards|EdwardsCareers|wd5", "Edwards Lifesciences"),  # to verify
    ("workday", "lnw|LightWonderExternalCareers|wd5", "Light & Wonder"),  # to verify
]


def _req(method: str, url: str, **kw) -> requests.Response:
    """requests with a retry on 429 (Eightfold rate-limits bursts): a missing description would be judged on the title alone."""
    kw.setdefault("timeout", TIMEOUT)
    for i in range(3):
        r = requests.request(method, url, **kw)
        if r.status_code == 429 and i < 2:
            time.sleep(4 * (i + 1))
            continue
        return r
    return r


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


def _parse_day(text: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            d = datetime.strptime(text[:len(text) if "%z" in fmt else 32].strip(), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ Amazon (descriptions are in the list)

def _amazon(cfg: str, name: str, since: datetime) -> list[dict]:
    out: list[dict] = []
    for offset in range(0, 100, 25):
        r = _req("GET", "https://www.amazon.jobs/en/search.json", headers=JSON, timeout=TIMEOUT, params={
            "base_query": "", "loc_query": "India", "country": "IND", "sort": "recent", "result_limit": 25, "offset": offset})
        r.raise_for_status()
        jobs = r.json().get("jobs") or []
        for j in jobs:
            posted = _parse_day(j.get("posted_date") or "")
            if posted and posted < since:
                return out
            desc = _strip(" ".join(filter(None, [j.get("description"), "Basic qualifications: " + (j.get("basic_qualifications") or ""),
                                                  "Preferred qualifications: " + (j.get("preferred_qualifications") or "")])))
            out.append({"id": f"amzn:{j.get('id_icims') or j.get('id')}", "title": j.get("title", ""), "company": "Amazon",
                        "location": j.get("normalized_location") or j.get("location") or "India",
                        "posted": _iso(posted) if posted else "", "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
                        "description": desc, "work_type": j.get("job_schedule_type") or ""})
        if len(jobs) < 25:
            break
    return out


# ------------------------------------------------------------------ Eightfold (PCSX)

def _eightfold(cfg: str, name: str, since: datetime) -> list[dict]:
    host, domain = cfg.split("|")
    out: list[dict] = []
    for start in range(0, 100, 10):
        r = _req("GET", f"https://{host}/api/pcsx/search", headers=JSON, timeout=TIMEOUT, params={
            "domain": domain, "query": "", "location": "India", "start": start, "sort_by": "timestamp"})
        r.raise_for_status()
        pos = ((r.json().get("data") or {}).get("positions")) or []
        for p in pos:
            ts = int(p.get("postedTs") or p.get("creationTs") or 0)
            posted = datetime.fromtimestamp(ts, timezone.utc) if ts else None
            if posted and posted < since:
                return out
            locs = p.get("locations")
            loc = ", ".join(locs) if isinstance(locs, list) else str(locs or "")
            out.append({"id": f"ef:{name}:{p['id']}", "title": p.get("name", ""), "company": name, "location": loc or "India",
                        "posted": _iso(posted) if posted else "", "url": f"https://{host}{p.get('positionUrl') or '/careers/job/' + str(p['id'])}",
                        "_ef": (host, domain, p["id"])})
        if len(pos) < 10:
            break
    return out


def _eightfold_detail(card: dict) -> str:
    host, domain, pid = card["_ef"]
    r = _req("GET", f"https://{host}/api/pcsx/position_details", headers=JSON, timeout=TIMEOUT,
                     params={"position_id": pid, "domain": domain, "hl": "en"})
    d = (r.json().get("data") or {}) if r.ok else {}
    return _strip(d.get("jobDescription") or "")


# ------------------------------------------------------------------ Phenom (widgets)

def _phenom(cfg: str, name: str, since: datetime) -> list[dict]:
    host, lang, country = cfg.split("|")
    out: list[dict] = []
    for start in range(0, 100, 20):
        body = {"lang": lang, "deviceType": "desktop", "country": country, "pageName": "search-results", "ddoKey": "refineSearch",
                "sortBy": "Most recent", "subsearch": "", "from": start, "jobs": True, "counts": False, "all_fields": ["country"],
                "size": 20, "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page11", "siteType": "external",
                "location": "", "keywords": "", "global": True, "selected_fields": {"country": ["India"]},
                "sort": {"order": "desc", "field": "postedDate"}, "locationData": {}}
        r = _req("POST", f"https://{host}/widgets", json=body, headers={**JSON, "Content-Type": "application/json"}, timeout=TIMEOUT)
        r.raise_for_status()
        jobs = (((r.json().get("refineSearch") or {}).get("data")) or {}).get("jobs") or []
        for j in jobs:
            posted = _parse_day(j.get("postedDate") or "")
            if posted and posted < since:
                return out
            rid = j.get("jobId") or j.get("reqId")
            url = j.get("applyUrl") if str(j.get("externalApply")).lower() == "true" else ""
            out.append({"id": f"ph:{name}:{rid}", "title": j.get("title", ""), "company": name,
                        "location": j.get("cityStateCountry") or j.get("location") or "India",
                        "posted": _iso(posted) if posted else "", "url": url or f"https://{host}/{country}/{lang.split('_')[0]}/job/{rid}",
                        "description": _strip(j.get("descriptionTeaser") or ""), "work_type": j.get("type") or "",
                        "_ph": f"https://{host}/{country}/{lang.split('_')[0]}/job/{rid}"})
        if len(jobs) < 20:
            break
    return out


# ------------------------------------------------------------------ Oracle HCM

def _oracle(cfg: str, name: str, since: datetime) -> list[dict]:
    host, site = cfg.split("|")
    out: list[dict] = []
    for offset in range(0, 100, 25):
        r = _req("GET", f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions", headers=JSON, timeout=TIMEOUT, params={
            "onlyData": "true", "expand": "requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={site},keyword=India,limit=25,offset={offset},sortBy=POSTING_DATES_DESC"})
        r.raise_for_status()
        items = (r.json().get("items") or [{}])[0].get("requisitionList") or []
        for j in items:
            posted = _parse_day(j.get("PostedDate") or "")
            if posted and posted < since:
                return out
            loc = j.get("PrimaryLocation") or ""
            if not INDIA.search(loc):
                continue
            out.append({"id": f"or:{name}:{j['Id']}", "title": j.get("Title", ""), "company": name, "location": loc,
                        "posted": _iso(posted) if posted else "", "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}",
                        "work_type": j.get("JobSchedule") or "", "_or": (host, site, j["Id"])})
        if len(items) < 25:
            break
    return out


def _oracle_detail(card: dict) -> str:
    host, site, rid = card["_or"]
    r = _req("GET", f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails", headers=JSON, timeout=TIMEOUT,
                     params={"expand": "all", "onlyData": "true", "finder": f'ById;Id="{rid}",siteNumber={site}'})
    d = ((r.json().get("items") or [{}])[0]) if r.ok else {}
    return _strip(" ".join(filter(None, [d.get("ExternalDescriptionStr"), d.get("ExternalResponsibilitiesStr"), d.get("ExternalQualificationsStr")])))


# ------------------------------------------------------------------ Workday

def _workday(cfg: str, name: str, since: datetime) -> list[dict]:
    tenant, site, host = cfg.split("|")
    base = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    out: list[dict] = []
    for offset in range(0, 120, 20):
        r = _req("POST", f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": "India"},
                          headers={**JSON, "Content-Type": "application/json"}, timeout=TIMEOUT)
        r.raise_for_status()
        page = r.json().get("jobPostings") or []   # raises ValueError on the maintenance page: the caller skips this company
        for j in page:
            on = j.get("postedOn") or ""
            days = 0 if "Today" in on else 1 if "Yesterday" in on else int(m.group(1)) if (m := re.search(r"(\d+)\+?\s*Day", on)) else None
            posted = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
            if posted is not None and posted < since - timedelta(days=1):  # day-level dates: be generous by a day
                continue  # Workday's default order is not newest-first: skip, don't stop
            loc = j.get("locationsText") or ""
            if not INDIA.search(loc) and not re.match(r"\d+ Locations", loc):
                continue
            path = j.get("externalPath") or ""
            out.append({"id": f"wd:{name}:{path.rsplit('/', 1)[-1]}", "title": j.get("title", ""), "company": name, "location": loc,
                        "posted": _iso(posted) if posted else "", "url": f"https://{tenant}.{host}.myworkdayjobs.com/{site}{path}",
                        "_wd": (base, path)})
        if len(page) < 20:
            break
    return out


def _workday_detail(card: dict) -> tuple[str, str]:
    base, path = card["_wd"]
    r = _req("GET", f"{base}{path}", headers=JSON, timeout=TIMEOUT)
    d = (r.json().get("jobPostingInfo") or {}) if r.ok else {}
    return _strip(d.get("jobDescription") or ""), d.get("location") or ""


# ------------------------------------------------------------------ Cognizant (full jobs feed, newest first: only the top is read)

def _cognizant(cfg: str, name: str, since: datetime) -> list[dict]:
    """careers.cognizant.com publishes every job as one XML feed (title, date, url, city/state/country, description), newest
    first. The feed is ~11 MB: it is streamed and reading stops at the first jobs older than `since`."""
    out: list[dict] = []
    r = _req("GET", "https://careers.cognizant.com/global-en/jobs/xml/?rss=true", headers={"User-Agent": UA}, stream=True, timeout=60)
    r.raise_for_status()
    buf, old, read = "", 0, 0
    for chunk in r.iter_content(65536):
        read += len(chunk)
        buf += chunk.decode("utf-8", "ignore")
        while True:
            a, b = buf.find("<job>"), buf.find("</job>")
            if a < 0 or b < 0:
                break
            block, buf = buf[a:b], buf[b + 6:]

            def f(tag: str) -> str:
                m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
                return m.group(1).strip() if m else ""

            try:
                posted = datetime.strptime(f("date"), "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except ValueError:
                posted = None
            if posted and posted < since:
                old += 1
                if old >= 5:  # a few out-of-order jobs are tolerated, then the rest is older
                    r.close()
                    return out
                continue
            if "india" not in f("country").lower():
                continue
            loc = ", ".join(x for x in (f("city"), f("state"), "India") if x)
            out.append({"id": f"cg:{f('requisitionid')}", "title": _strip(f("title")), "company": "Cognizant", "location": loc,
                        "posted": _iso(posted) if posted else "", "url": f("url"), "description": _strip(f("description"))})
        if read > 8_000_000:
            break
    r.close()
    return out


# ------------------------------------------------------------------ Wipro (SuccessFactors recruiting service)

def _wipro(cfg: str, name: str, since: datetime) -> list[dict]:
    out: list[dict] = []
    for page in range(0, 12):
        body = {"locale": "en_US", "pageNumber": page, "sortBy": "date", "keywords": "", "location": "India", "facetFilters": {},
                "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""}
        r = _req("POST", f"https://{cfg}/services/recruiting/v1/jobs", json=body, headers={**JSON, "Content-Type": "application/json"})
        r.raise_for_status()
        res = r.json().get("jobSearchResult") or []
        for it in res:
            x = it.get("response") or {}
            try:
                posted = datetime.strptime(x.get("unifiedStandardStart") or "", "%m/%d/%y").replace(tzinfo=timezone.utc)
            except ValueError:
                posted = None
            if posted and posted < since - timedelta(days=1):
                return out  # date-sorted: the rest is older
            locs = x.get("sfstd_jobLocation_obj") or x.get("jobLocationShort") or []
            loc = ", ".join(str(v) for v in (locs if isinstance(locs, list) else [locs])) + ", India"
            title = x.get("unifiedStandardTitle") or ""
            out.append({"id": f"wp:{x.get('id')}", "title": title, "company": name, "location": loc,
                        "posted": _iso(posted) if posted else "",
                        "url": f"https://{cfg}/job/{x.get('unifiedUrlTitle') or x.get('urlTitle')}/{x.get('id')}-en_US",
                        "_sf": f"https://{cfg}/job/{x.get('unifiedUrlTitle') or x.get('urlTitle')}/{x.get('id')}-en_US"})
        if len(res) < 10:
            break
    return out


# ------------------------------------------------------------------ SuccessFactors job-list pages (Capgemini)

def _sf_html(cfg: str, name: str, since: datetime) -> list[dict]:
    from bs4 import BeautifulSoup

    out: list[dict] = []
    for start in range(0, 150, 25):
        r = _req("GET", f"https://{cfg}/search/", headers={"User-Agent": UA}, params={
            "q": "", "locationsearch": "India", "sortColumn": "referencedate", "sortDirection": "desc", "startrow": start})
        r.raise_for_status()
        rows = BeautifulSoup(r.text, "html.parser").select("tr.data-row")
        for row in rows:
            a = row.select_one("a.jobTitle-link")
            if not a:
                continue
            d = row.select_one(".jobDate")
            posted = _parse_day(re.sub(r"\s+", " ", d.get_text()).strip()) if d else None
            try:
                posted = posted or datetime.strptime(re.sub(r"\s+", " ", d.get_text()).strip(), "%b %d, %Y").replace(tzinfo=timezone.utc)
            except Exception:  # noqa: BLE001
                pass
            if posted and posted < since - timedelta(days=1):
                return out
            loc = row.select_one(".colLocation .jobLocation")
            out.append({"id": f"sf:{name}:{a['href'].strip('/').rsplit('/', 1)[-1]}", "title": a.get_text(strip=True), "company": name,
                        "location": re.sub(r"\s+", " ", loc.get_text(" ")).replace("+2 more…", "").strip() if loc else "India",
                        "posted": _iso(posted) if posted else "", "url": f"https://{cfg}{a['href']}", "_sf": f"https://{cfg}{a['href']}"})
        if len(rows) < 25:
            break
    return out


def _sf_detail(card: dict) -> str:
    from bs4 import BeautifulSoup

    r = _req("GET", card["_sf"], headers={"User-Agent": UA})
    if not r.ok:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    e = soup.select_one(".jobdescription")
    if e:
        return re.sub(r"\s+", " ", e.get_text(" ", strip=True))
    spans = [re.sub(r"\s+", " ", x.get_text(" ", strip=True)) for x in soup.select('[itemprop="description"]')]  # Wipro: company blurb + the posting
    return " ".join(sp for sp in spans if sp)[:8000]


def _phenom_detail(card: dict) -> str:
    """The job page embeds the full posting as JSON ("description":"<html>"); the list only has a one-line teaser."""
    import json as _json
    r = _req("GET", card["_ph"], headers={"User-Agent": UA})
    if not r.ok:
        return ""
    best, i, dec = "", r.text.find('"description":"'), _json.JSONDecoder()
    while i >= 0:
        try:
            val, _ = dec.raw_decode(r.text[i + len('"description":'):])
            if isinstance(val, str) and len(val) > len(best):
                best = val
        except ValueError:
            pass
        i = r.text.find('"description":"', i + 10)
    return _strip(unescape(best))


LISTERS = {"cognizant": _cognizant, "wipro": _wipro, "sf_html": _sf_html, "amazon": _amazon, "eightfold": _eightfold, "phenom": _phenom, "oracle": _oracle, "workday": _workday}


def list_jobs(entry: tuple[str, str, str], since: datetime) -> list[dict]:
    kind, cfg, name = entry
    return LISTERS[kind](cfg, name, since)


def detail(card: dict) -> dict:
    """Fills card["description"] (and the location for Workday's "N Locations")."""
    try:
        if "_ef" in card:
            card["description"] = _eightfold_detail(card)
        elif "_or" in card:
            card["description"] = _oracle_detail(card)
        elif "_ph" in card:
            full = _phenom_detail(card)
            if len(full) > len(card.get("description", "")):
                card["description"] = full
        elif "_sf" in card:
            card["description"] = _sf_detail(card)
        elif "_wd" in card:
            desc, where = _workday_detail(card)
            card["description"] = desc
            if where and re.match(r"\d+ Locations", card.get("location", "")):
                card["location"] = where
    except Exception:  # noqa: BLE001 — a job without a description is still judged by its title
        pass
    time.sleep(0.2)
    return card
