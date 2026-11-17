"""Reads verification codes / links straight from the owner's Gmail (IMAP + app password): no Telegram step, nothing manual.
env: GMAIL_USER, GMAIL_APP_PASSWORD. Only mails that arrived after `since` and that look like they come from the portal are used.
Nothing from a mail is ever printed (the repo's logs are public)."""
from __future__ import annotations

import email
import imaplib
import os
import re
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from html import unescape

CODE_NEAR = re.compile(r"(?:code|otp|passcode|pin|verification|verify|security)[^0-9]{0,60}\b(\d{4,8})\b", re.I)
CODE_ALONE = re.compile(r"(?m)^\s*(\d{6})\s*$")
LINK = re.compile(r"https?://[^\s\"'<>)]+", re.I)
LINK_WORDS = re.compile(r"(verify|verification|confirm|activate|activation|validate|token|registration|register|account|auth)", re.I)
BAD_LINK = re.compile(r"(unsubscribe|privacy|facebook|twitter|linkedin\.com/company|instagram|youtube|\.png|\.jpg|\.gif|googleusercontent|w3\.org|mailto:)", re.I)


def configured() -> bool:
    return bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))


def _text(msg: email.message.Message) -> str:
    out = []
    for part in msg.walk():
        ct = part.get_content_type()
        if ct not in ("text/plain", "text/html"):
            continue
        try:
            body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if ct == "text/html":
            links = re.findall(r'href=["\']([^"\']+)["\']', body, re.I)
            body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
            body = unescape(re.sub(r"<[^>]+>", "\n", body)) + "\n" + "\n".join(unescape(x) for x in links)
        out.append(body)
    return "\n".join(out)


def _hdr(v: str) -> str:
    try:
        return "".join((t.decode(c or "utf-8", "replace") if isinstance(t, bytes) else t) for t, c in decode_header(v or ""))
    except Exception:  # noqa: BLE001
        return v or ""


def _tokens(host: str) -> list[str]:
    parts = [p for p in re.split(r"[.\-]", host.lower()) if p and p not in ("www", "com", "in", "co", "org", "net", "careers", "career", "jobs", "wd1", "wd3", "wd5", "wd103", "myworkdayjobs", "taleo", "eu", "io")]
    return [p for p in parts if len(p) >= 4] + ["successfactors", "workday", "icims", "smartrecruiters", "greenhouse", "lever"] * 0


def _scan(m: imaplib.IMAP4_SSL, folder: str, since: float, tokens: list[str], want: str):
    try:
        if m.select(folder, readonly=True)[0] != "OK":
            return None
    except Exception:  # noqa: BLE001
        return None
    day = time.strftime("%d-%b-%Y", time.gmtime(since - 86400))
    typ, data = m.search(None, f'(SINCE "{day}")')
    ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-25:]
    cands = []
    for i in reversed(ids):
        typ, d = m.fetch(i, "(RFC822)")
        if typ != "OK" or not d or not d[0]:
            continue
        msg = email.message_from_bytes(d[0][1])
        try:
            ts = parsedate_to_datetime(msg["Date"]).timestamp()
        except Exception:  # noqa: BLE001
            ts = time.time()
        if ts < since:
            continue
        head = f"{_hdr(msg['From'])} {_hdr(msg['Subject'])}".lower()
        body = _text(msg)
        low = (head + " " + body[:3000]).lower()
        brand = any(t in low for t in tokens) if tokens else True
        looks = bool(re.search(r"(verif|confirm|activat|one[- ]time|otp|passcode|security code|registration|welcome|your account)", head + " " + body[:600], re.I))
        if looks:
            cands.append((brand, ts, body))
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    for brand, ts, body in cands:
        if not brand and len(cands) > 1:
            continue   # several unrelated mails and none mentions the portal: do not guess
        code = CODE_NEAR.search(body) or CODE_ALONE.search(body)
        links = [u.rstrip(".,;") for u in LINK.findall(body) if LINK_WORDS.search(u) and not BAD_LINK.search(u)]
        if want in ("code", "any") and code:
            return ("code", code.group(1))
        if want in ("link", "any") and links:
            return ("link", links[0])
        if want == "link" and code:
            return ("code", code.group(1))
        if want == "code" and links:
            return ("link", links[0])
    return None


def wait(host: str, since: float, want: str = "any", minutes: float = 4.0, brands: tuple | list = ()) -> tuple[str, str] | None:
    """('code', '123456') | ('link', 'https://...') | None. Polls Gmail every ~8 s (inbox, then spam)."""
    if not configured():
        return None
    tokens = _tokens(host) + [w for b in brands for w in re.split(r"[^a-z0-9]+", str(b).lower()) if len(w) >= 4 and w not in ("limited", "private", "technologies", "services", "india", "solutions")]
    end = time.time() + minutes * 60
    while time.time() < end:
        try:
            m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
            m.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
            try:
                for folder in ("INBOX", "[Gmail]/Spam"):
                    r = _scan(m, folder, since, tokens, want)
                    if r:
                        return r
            finally:
                try:
                    m.logout()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        time.sleep(8)
    return None
