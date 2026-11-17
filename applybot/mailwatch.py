"""Forwards every verification / one-time-code email from your Gmail to Telegram within seconds (runs as a thread in the local runner).
Only mails that look like codes / verification links are forwarded; nothing is stored. Needs GMAIL_USER + GMAIL_APP_PASSWORD (read from .env by the runner)."""
from __future__ import annotations

import email
import imaplib
import os
import re
import threading
import time
from email.utils import parseaddr, parsedate_to_datetime

from . import cloud
from .mail import BAD_LINK, CODE_ALONE, CODE_NEAR, LINK, LINK_WORDS, _hdr, _text

LOOKS = re.compile(r"(verification code|verify your|confirm your (e-?mail|account)|one[- ]time|\botp\b|passcode|security code|login code|sign[- ]in code|activate your account|your code is|is your .{0,30}code|"
                   r"confirm(ation)? (link|code)|authentication code|account verification|complete your registration)", re.I)


def _forward(text: str) -> None:
    try:
        cloud._call("POST", "/api/apply/notify", json={"text": text[:900]})
    except Exception:  # noqa: BLE001
        pass


def _loop(stop: threading.Event) -> None:
    started = time.time() - 120
    seen: set[str] = set()
    while not stop.is_set():
        try:
            m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
            m.login(os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
            try:
                for folder in ("INBOX", "[Gmail]/Spam"):
                    if m.select(folder, readonly=True)[0] != "OK":
                        continue
                    day = time.strftime("%d-%b-%Y", time.gmtime(started - 86400))
                    typ, data = m.search(None, f'(SINCE "{day}")')
                    ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-15:]
                    for i in reversed(ids):
                        typ, d = m.fetch(i, "(RFC822)")
                        if typ != "OK" or not d or not d[0]:
                            continue
                        msg = email.message_from_bytes(d[0][1])
                        mid = str(msg.get("Message-ID") or i)
                        if mid in seen:
                            continue
                        try:
                            ts = parsedate_to_datetime(msg["Date"]).timestamp()
                        except Exception:  # noqa: BLE001
                            ts = time.time()
                        if ts < started:
                            seen.add(mid)
                            continue
                        seen.add(mid)
                        subject = _hdr(msg["Subject"])
                        body = _text(msg)
                        if not LOOKS.search(subject + " " + body[:800]):
                            continue
                        sender = parseaddr(_hdr(msg["From"]))[1].split("@")[-1] or "?"
                        code = CODE_NEAR.search(body) or CODE_ALONE.search(body)
                        links = [u.rstrip(".,;") for u in LINK.findall(body) if LINK_WORDS.search(u) and not BAD_LINK.search(u)]
                        parts = [f"🔐 <b>{sender}</b>: {subject[:80]}"]
                        if code:
                            parts.append(f"Code: <code>{code.group(1)}</code>")
                        if links:
                            parts.append(f"Link: {links[0][:300]}")
                        _forward("\n".join(parts))
            finally:
                try:
                    m.logout()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        stop.wait(20)


def start() -> threading.Event | None:
    if not (os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD")):
        return None
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), daemon=True).start()
    return stop
