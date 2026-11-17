"""The apply loop: look (snapshot) -> classify -> act -> check progress. One job at a time.

Returns (status, detail): applied | needs_you | failed | closed | blocked | skipped.
Nothing personal is ever printed: logs carry page kinds, field LABELS and counts, never the values typed.
"""
from __future__ import annotations

import re
import time
from typing import Any

from . import actions as act
from . import cloud
from .answers import resolve
from .classify import APPLY_WORDS, CREATE_BTN, FINAL, NEXT, classify
from .snap import locate, snapshot, state_key

AVOID = re.compile(r"^(accept( all)?( cookies)?|reject( all)?( cookies)?|cookie|manage (cookies|preferences)|modify cookie|cookies? (settings|preferences)|got it|i understand|allow (all|cookies)|back|previous|cancel|close|discard|save for later|sign ?out|log ?out|help|decline|reject|withdraw|skip|no thanks|not now|dismiss|clear|reset|edit|delete|remove|add another|add (a )?(work|education|experience|language|skill|website)|"
                   r"forgot|learn more|view|download|print|share|menu|search|filter|upload|apply with (linkedin|indeed|google|facebook|apple|seek)|sign in with|continue with (google|linkedin|facebook|apple)|google|linkedin|facebook|apple)\b", re.I)
SOCIAL = re.compile(r"(google|linkedin|facebook|apple|microsoft|github|indeed|seek)\b", re.I)
class Stuck(Exception):
    pass


MAX_STEPS = 70
MAX_SECONDS = 15 * 60


class Run:
    def __init__(self, ctx, job: dict, profile: dict, accounts: list[str], resume_path: str, dry: bool):
        self.ctx, self.job, self.profile, self.accounts = ctx, job, profile, set(accounts)
        self.resume, self.dry = resume_path, dry
        self.page = ctx.new_page()
        self.steps: list[str] = []
        self.created: set[str] = set()      # domains where an account was created in this run
        self.tried_signin: set[str] = set()
        self.clicked_create: set[str] = set()
        self.fill_rounds = 0
        self.clicks: dict[str, int] = {}
        self.t0 = time.time()
        self.email = profile["personal"]["email"]

    # ------------------------------------------------------------------ helpers
    def log(self, msg: str) -> None:
        line = f"{int(time.time() - self.t0):>3}s {msg}"
        self.steps.append(line)
        print("   " + line, flush=True)

    def adopt_newest_page(self) -> None:
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        if pages and pages[-1] != self.page:
            self.page = pages[-1]
            self.log("switched to the new tab")

    COOKIE_NO = re.compile(r"^(reject( all)?( non-?essential)?( cookies)?|decline( all)?( cookies)?|deny( all)?|only (necessary|essential)( cookies)?|(necessary|essential) (cookies )?only|continue without accepting|refuse( all)?|no,? thanks)$", re.I)

    def dismiss_cookies(self) -> None:
        """Privacy-friendly choice on a cookie banner (reject / necessary only). A banner with only 'Accept' is left alone."""
        try:
            btn = self.page.get_by_role("button", name=self.COOKIE_NO).first
            if btn.is_visible(timeout=300):
                btn.click(timeout=2000)
                self.log("cookie banner: rejected non-essential")
                self.page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass

    def settle(self, ms: int = 1200) -> None:
        self.adopt_newest_page()
        self.dismiss_cookies()
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        self.page.wait_for_timeout(ms)
        try:
            self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:  # noqa: BLE001
            pass

    def ai(self, system: str, user: str, schema: dict):
        return cloud.ai(system, user, schema)

    # ------------------------------------------------------------------ main loop
    def run(self) -> tuple[str, str]:
        url = self.job["url"]
        err = ""
        for attempt in (0, 1, 2):
            try:
                self.page.goto(url, wait_until="commit" if attempt else "domcontentloaded", timeout=45000)
                err = ""
                break
            except Exception as e:  # noqa: BLE001
                err = str(e)[:80]
                self.log(f"open attempt {attempt + 1} failed: {err}")
                self.page.wait_for_timeout(3000)
        if err:
            return "failed", "Couldn't open the apply link"
        last, same = "", 0
        for step in range(MAX_STEPS):
            if time.time() - self.t0 > MAX_SECONDS:
                return "needs_you", "Took too long, finish it by hand"
            self.settle()
            if step % 4 == 0:
                cloud.alive(self.job["id"])
            snap = snapshot(self.page)
            kind = classify(snap)
            key = kind + "|" + state_key(snap)
            same = same + 1 if key == last else 0
            last = key
            self.log(f"{kind} @ {snap['host']} fields={len(snap['fields'])} buttons={len(snap['buttons'])}" + (f" errors={len(snap['errors'])}" if snap["errors"] else ""))
            if same >= 3:
                return "needs_you", f"Stuck on a {kind} page (nothing changed after 3 tries)"
            try:
                res = self.handle(kind, snap, same)
            except Stuck as e:
                return "needs_you", f"Stuck: {e}"
            if res:
                return res
        return "needs_you", "Too many steps, finish it by hand"

    def handle(self, kind: str, snap: dict, same: int) -> tuple[str, str] | None:
        if kind == "submitted":
            return "applied", "Submitted (the page confirms it)"
        if kind == "already_applied":
            return "applied", "Already applied earlier"
        if kind == "closed":
            return "closed", "The posting is closed or removed"
        if kind in ("captcha", "blocked"):
            if same == 0:
                self.page.wait_for_timeout(7000)   # some checks clear by themselves
                return None
            return "blocked", "Bot check / CAPTCHA: needs you"
        if kind == "account_exists":   # a previous try already created it: use the password we derive for this portal
            self.created.add(snap["host"])
            cloud.account(snap["host"], self.email)
            b = next((x for x in snap["buttons"] if re.match(r"^(sign ?in|log ?in|login)$", x["text"], re.I) and not x["disabled"]), None)
            if not b:
                return "needs_you", f"An account for {snap['host']} already exists and I can't find its sign-in button"
            self.press(snap, b)
            return None
        if kind == "otp":
            return self.do_otp(snap)
        if kind == "verify_email":
            return self.do_verify_email(snap)
        if kind == "signin":
            return self.do_signin(snap)
        if kind == "create_account":
            return self.do_create(snap)
        if kind == "job_detail":
            return self.do_job_detail(snap)
        if kind == "form":
            return self.do_form(snap)
        return self.do_unknown(snap, same)

    def press(self, snap: dict, b: dict) -> None:
        """Click a button; the same control (kind + text + link target) pressed 3 times means we are going in circles."""
        key = f"{snap['host']}|{'a' if b['link'] else 'b'}|{b['text'].lower()}|{b.get('href', '')[:80]}"
        self.clicks[key] = self.clicks.get(key, 0) + 1
        if self.clicks[key] > 3:
            raise Stuck(f"pressing '{b['text']}' again and again changes nothing")
        self.log(f"click '{b['text']}'")
        act.click(self.page, locate(snap, b["id"]))

    # ------------------------------------------------------------------ job page -> application
    def do_job_detail(self, snap: dict) -> tuple[str, str] | None:
        cands = [b for b in snap["buttons"] if APPLY_WORDS.match(b["text"]) and not b["disabled"] and not SOCIAL.search(b["text"])]
        if not cands:
            cands = [b for b in snap["buttons"] if re.search(r"\bapply\b", b["text"], re.I) and not b["disabled"] and not AVOID.match(b["text"])]
        if not cands:
            return "needs_you", "No Apply button found on the job page"
        order = ["apply manually", "apply now", "apply", "start", "i'm interested", "apply for this job", "apply online"]
        prev = getattr(self, "_prev_buttons", set())
        # a dropdown that just opened shows NEW buttons (Wipro: "Apply now" -> menu item "Apply Now"): take those first
        cands.sort(key=lambda b: (0 if (b["link"], b["text"].lower(), b.get("href", "")) not in prev else 1,
                                  next((i for i, o in enumerate(order) if b["text"].lower().startswith(o)), 99)))
        self._prev_buttons = {(x["link"], x["text"].lower(), x.get("href", "")) for x in snap["buttons"]}
        b = cands[0]
        if self.dry and re.match(r"^(submit|send|finish|complete|confirm)", b["text"], re.I):
            return "needs_you", "DRY RUN: stopped before the final Submit"
        self.press(snap, b)
        return None

    # ------------------------------------------------------------------ accounts
    def email_pw(self, host: str) -> tuple[str, str]:
        return self.email, cloud.password_for(host)

    def _fill_by_kind(self, snap: dict, mapping: list[tuple[Any, str]]) -> None:
        for f, v in mapping:
            act.set_text(self.page, locate(snap, f["id"]), v)

    def _find_link(self, snap: dict, rx: re.Pattern) -> dict | None:
        for b in snap["buttons"]:
            if rx.search(b["text"]) and not b["disabled"] and not SOCIAL.search(b["text"]):
                return b
        return None

    def do_signin(self, snap: dict) -> tuple[str, str] | None:
        host = snap["host"]
        email, pw = self.email_pw(host)
        known = host in self.accounts or host in self.created
        make = self._find_link(snap, re.compile(r"(create (an )?account|register|sign ?up|new (user|candidate)|don't have an account|join)", re.I))
        if not known and make and host not in self.clicked_create:
            self.clicked_create.add(host)
            self.log("no account here yet: opening 'create account'")
            act.click(self.page, locate(snap, make["id"]))
            return None
        # signed in once already on this page with the same inputs and still here: wrong password / no account
        if host in self.tried_signin:
            if make and host not in self.clicked_create:
                self.clicked_create.add(host)
                self.log("sign-in did not work: trying 'create account'")
                act.click(self.page, locate(snap, make["id"]))
                return None
            return "needs_you", f"Couldn't sign in to {host} (check the account / password)"
        self.tried_signin.add(host)
        for f in snap["fields"]:
            if f["kind"] == "password":
                act.set_text(self.page, locate(snap, f["id"]), pw)
            elif f["kind"] in ("email", "text") and not f["value"]:
                act.set_text(self.page, locate(snap, f["id"]), email)
        self.log("signing in")
        return self._press(snap, re.compile(r"^(sign ?in|log ?in|login|continue|next|submit)$", re.I))

    DOC_LINK = re.compile(r"((read|review|view|open)( and)?( accept| agree)?.*(privacy|terms|policy|statement|agreement|conditions|notice))|((accept|agree to|i agree( to)?|i accept).*(privacy|terms|policy|statement|agreement|conditions))", re.I)
    DOC_OK = re.compile(r"^(accept|i accept|accept (all|and continue|terms)|agree|i agree|agree and continue|ok|okay|continue|confirm|done|close|yes)$", re.I)

    def accept_documents(self, snap: dict) -> bool:
        """Portals that make you OPEN the privacy statement / terms and press Accept before the box can be ticked or the form sent."""
        host = snap["host"]
        if getattr(self, "_docs_done", None) is None:
            self._docs_done = set()
        for b in snap["buttons"]:
            if b["disabled"] or not self.DOC_LINK.search(b["text"]) or SOCIAL.search(b["text"]) or f"{host}|{b['text']}" in self._docs_done:
                continue
            self._docs_done.add(f"{host}|{b['text']}")
            self.log(f"opening '{b['text'][:50]}' to accept it")
            home = self.page
            n0 = len(self.ctx.pages)
            act.click(self.page, locate(snap, b["id"]))
            self.page.wait_for_timeout(1500)
            opened = len(self.ctx.pages) > n0
            if opened:
                self.page = [p for p in self.ctx.pages if not p.is_closed()][-1]
            s2 = snapshot(self.page)
            ok = next((x for x in s2["buttons"] if self.DOC_OK.match(x["text"]) and not x["disabled"]), None)
            if ok:
                self.log(f"pressing '{ok['text']}'")
                act.click(self.page, locate(s2, ok["id"]))
                self.page.wait_for_timeout(1000)
            if opened and not self.page.is_closed() and self.page != home:   # a document opened in its own tab: back to the form
                self.page.close()
            self.page = home
            return True
        return False

    def untick_marketing(self, snap: dict) -> None:
        from .answers import MARKETING
        for f in snap["fields"]:
            if f["kind"] == "checkbox" and f.get("checked") and MARKETING.search(f["label"]) and not f["required"]:
                act.set_checkbox(self.page, locate(snap, f["id"]), False)

    def do_create(self, snap: dict) -> tuple[str, str] | None:
        host = snap["host"]
        email, pw = self.email_pw(host)
        pwf = [f for f in snap["fields"] if f["kind"] == "password"]
        for f in pwf:
            act.set_text(self.page, locate(snap, f["id"]), pw)
        others = [f for f in snap["fields"] if f["kind"] != "password"]
        ans = resolve(others, self.profile, self.job, self.ai)
        for f in others:
            a = ans.get(f["id"])
            if a and a["value"]:
                act.apply_answer(self.page, snap, f, a)
        for f in others:   # required consent boxes on a sign-up form are part of creating the account
            if f["kind"] == "checkbox" and not f.get("checked") and f["required"]:
                act.set_checkbox(self.page, locate(snap, f["id"]), True)
        self.untick_marketing(snap)
        if (any(re.search(r"privacy|terms|agreement|statement|accept", e, re.I) for e in snap["errors"]) or True) and self.accept_documents(snap):
            return None
        self.log("creating the account")
        self.created.add(host)
        r = self._press(snap, re.compile(r"(create|register|sign ?up|join|submit|continue|next)", re.I))
        if not r:
            cloud.account(host, email)
        return r

    def do_otp(self, snap: dict) -> tuple[str, str] | None:
        host = snap["host"]
        req = cloud.otp_request(self.job["id"], "code", "", host)
        self.log("waiting for the verification code from Telegram")
        code = cloud.otp_wait(req, self.job["id"])
        if not code:
            return "needs_you", f"{host} wanted a verification code and none arrived in 10 minutes"
        if code.startswith("http"):
            self.page.goto(code, wait_until="domcontentloaded", timeout=40000)
            return None
        boxes = [f for f in snap["fields"] if f["kind"] in ("text", "number", "code", "tel", "password")]
        if len(boxes) >= 4 and all(f["maxlength"] == 1 for f in boxes[:4]):
            act.click(self.page, locate(snap, boxes[0]["id"]))
            self.page.keyboard.type(code, delay=80)
        elif boxes:
            act.set_text(self.page, locate(snap, boxes[0]["id"]), code)
        self.log("code entered")
        r = self._press(snap, re.compile(r"(verify|confirm|submit|continue|next|activate|validate)", re.I))
        if r is None and not any(re.search(r"(verify|confirm|submit|continue|next|activate|validate)", b["text"], re.I) for b in snap["buttons"] if not b["disabled"]):
            self.page.keyboard.press("Enter")
        return r

    def do_verify_email(self, snap: dict) -> tuple[str, str] | None:
        host = snap["host"]
        req = cloud.otp_request(self.job["id"], "link", "Open the email from this site and send me its verification link", host)
        self.log("waiting for the verification link from Telegram")
        val = cloud.otp_wait(req, self.job["id"])
        if not val:
            return "needs_you", f"{host} wants the email verified and nothing arrived in 10 minutes"
        if val.startswith("http"):
            self.page.goto(val, wait_until="domcontentloaded", timeout=40000)
        else:
            self.log("got a code instead of a link: looking for a box")
        # the link verified the account in this browser; the original apply link continues from the portal's own sign-in
        self.page.goto(self.job["url"], wait_until="domcontentloaded", timeout=40000)
        return None

    # ------------------------------------------------------------------ the form
    def do_form(self, snap: dict) -> tuple[str, str] | None:
        self.fill_rounds += 1
        self.untick_marketing(snap)
        fields = snap["fields"]
        # custom dropdowns: read their options first so the answer is matched to a real option
        for f in fields:
            if f["kind"] == "combo" and not f["value"] and not f["options"] and f["required"]:
                f["options"] = act.read_combo_options(self.page, locate(snap, f["id"]))
        ans = resolve(fields, self.profile, self.job, self.ai)
        done: set[str] = set()
        failed: list[str] = []
        for f in fields:
            a = ans.get(f["id"])
            if not a or not a["value"]:
                continue
            ok = False
            try:
                ok = act.apply_answer(self.page, snap, f, a)
            except Exception:  # noqa: BLE001
                ok = False
            (done.add(f["id"]) if ok else failed.append(f["label"][:50]))
        for f in fields:   # resume
            if f["kind"] == "file" and not f["value"]:
                if act.upload(self.page, locate(snap, f["id"]), self.resume):
                    done.add(f["id"])
                    self.log(f"resume uploaded ({f['label'][:30] or 'file'})")
        self.log(f"filled {len(done)} field(s)" + (f"; couldn't set: {', '.join(failed[:4])}" if failed else ""))
        self.page.wait_for_timeout(500)
        # look again: what is still empty / invalid?
        snap2 = snapshot(self.page)
        missing = []
        for f in snap2["fields"]:
            if f["kind"] in ("password",):
                continue
            empty = (not f["value"]) if f["kind"] != "checkbox" else not f.get("checked")
            if (f["required"] and empty and f["id"] not in done) or ((f["invalid"] or f["error"]) and f["required"]):
                missing.append(f)
        # the same fields on this page, answered again with the portal's error text (once per page)
        if missing and self.fill_rounds % 2 == 1:
            retry = [f for f in missing if f["kind"] not in ("file",)]
            ans2 = resolve([dict(f, value="", invalid=True) for f in retry], self.profile, self.job, self.ai)
            for f in retry:
                a = ans2.get(f["id"])
                if a and a["value"]:
                    try:
                        act.apply_answer(self.page, snap2, f, a)
                    except Exception:  # noqa: BLE001
                        pass
            self.page.wait_for_timeout(400)
            snap2 = snapshot(self.page)
            missing = [f for f in snap2["fields"] if f["kind"] != "password" and f["required"] and ((not f["value"]) if f["kind"] != "checkbox" else not f.get("checked"))
                       and not (f["id"] in done and f["kind"] in ("combo", "radio"))]
        buttons = [b for b in snap2["buttons"] if not b["disabled"] and not AVOID.match(b["text"]) and not SOCIAL.search(b["text"])]
        final = next((b for b in buttons if FINAL.match(b["text"])), None)
        nxt = next((b for b in buttons if NEXT.match(b["text"])), None)
        target = final or nxt
        if not target:
            target = next((b for b in buttons if b["primary"]), None)
        if missing:
            names = ", ".join(sorted({(f["label"] or f["name"] or f["kind"])[:60] for f in missing})[:6])
            return "needs_you", f"Couldn't answer: {names}"
        if not target:
            return "needs_you", "No Next / Submit button found"
        if final and FINAL.match(target["text"]) and self.dry:
            return "needs_you", "DRY RUN: everything filled, stopped before the final Submit"
        self.press(snap2, target)
        self.settle(1800)
        return None

    # ------------------------------------------------------------------ unknown page
    def do_unknown(self, snap: dict, same: int) -> tuple[str, str] | None:
        btns = [b for b in snap["buttons"] if not b["disabled"] and not AVOID.match(b["text"]) and not SOCIAL.search(b["text"])]
        pick = next((b for b in btns if APPLY_WORDS.match(b["text"]) or FINAL.match(b["text"]) or NEXT.match(b["text"])), None)
        if pick:
            self.press(snap, pick)
            return None
        if not snap["text"].strip() and not snap["fields"] and not snap["buttons"]:   # blank page (slow portal / failed script): wait, then reload once or twice
            self.blank = getattr(self, "blank", 0) + 1
            if self.blank in (2, 4):
                self.log("blank page: reloading")
                try:
                    self.page.reload(wait_until="domcontentloaded", timeout=40000)
                except Exception:  # noqa: BLE001
                    pass
            self.page.wait_for_timeout(6000)
            if self.blank < 7:
                return None
            return "needs_you", f"The page stayed blank ({snap['host']})"
        if same == 0:   # slow page: wait and look again
            self.page.wait_for_timeout(4000)
            return None
        return "needs_you", f"Unfamiliar page ({snap['title'][:60]})"

    def _press(self, snap: dict, rx: re.Pattern) -> tuple[str, str] | None:
        btn = None
        for b in snap["buttons"]:
            if b["disabled"] or SOCIAL.search(b["text"]) or AVOID.match(b["text"]):
                continue
            if rx.search(b["text"]):
                if not b["link"] or b.get("primary"):
                    btn = b
                    break
                btn = btn or b
        if not btn:
            self.page.keyboard.press("Enter")
            return None
        act.click(self.page, locate(snap, btn["id"]))
        self.settle(1800)
        return None
