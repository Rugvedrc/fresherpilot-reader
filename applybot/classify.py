"""What kind of page is this? Plain rules over the snapshot (no AI)."""
from __future__ import annotations

import re

SUBMITTED = re.compile(r"(application (has been |was |is )?(successfully )?(submitted|received|sent)|thank you for (applying|your application|submitting)|successfully (applied|submitted)|"
                       r"we('| ha)ve received your application|you('ve| have) (successfully )?applied|your application (is|has been) (complete|submitted|received)|application complete)", re.I)
CLOSED = re.compile(r"(no longer (accepting|available|open)|position (has been |is )?(filled|closed)|job (is |has been )?(closed|expired|removed|no longer)|"
                    r"(this )?(job|position|requisition|posting) (is )?(no longer|not) (available|active|open)|has (been )?expired|applications? (are |is )?closed|"
                    r"not currently accepting|page (you are looking for )?(cannot|can't|could not) be found|404|job not found|vacancy (is )?closed)", re.I)
ALREADY = re.compile(r"(you('ve| have) already applied|already applied (for|to) this|you have previously applied|application (already )?(exists|in progress)|already submitted an application)", re.I)
BLOCKED = re.compile(r"(access denied|you have been blocked|request unsuccessful|pardon our interruption|unusual traffic|are you a robot|verify you are (a )?human|"
                     r"checking your browser|just a moment|attention required|press (&|and) hold|security check)", re.I)
OTP_LABEL = re.compile(r"(verification|security|one[- ]?time|confirmation|activation|passcode|otp|authenticat)\w* ?(code|pin|password|number)?|^code$|enter (the )?code|6[- ]digit", re.I)
VERIFY_EMAIL = re.compile(r"(verify your email|check your (e-?mail|inbox)|we('| ha)ve sent (you )?(an )?(e-?mail|a link|a verification)|confirmation (e-?mail|link)|activate your account|"
                          r"click (on )?the link (in|we)|email (has been|was) sent|verification (e-?mail|link) (has been |was )?sent)", re.I)
APPLY_WORDS = re.compile(r"^(apply( now| here| for this job| for job| to this job| online| externally)?|apply manually|start (your )?application|i'?m interested|apply (on|at) (company|employer)|"
                         r"easy apply|submit (your )?application|begin application|apply with (linkedin|resume)|use my last application|quick apply|apply for this position)$", re.I)
FINAL = re.compile(r"^(submit( application| my application)?|send application|finish( application)?|apply|apply now|complete application|confirm( and submit)?|review and submit|submit & apply|agree and submit)$", re.I)
NEXT = re.compile(r"^(save (and|&) continue|next|continue|proceed|save and next|next step|go to next|continue to .+|save & next|save|review|next page|continue application)$", re.I)
SIGNIN_BTN = re.compile(r"^(sign ?in|log ?in|login|submit)$", re.I)
CREATE_BTN = re.compile(r"(create (an )?account|register|sign ?up|join now|get started)", re.I)


def classify(s: dict) -> str:
    text = s["text"]
    low = text.lower()
    fields = s["fields"]
    pw = [f for f in fields if f["kind"] == "password"]
    fillable = [f for f in fields if f["kind"] not in ("file",)]

    if s.get("captcha") and len(fillable) <= 4 and not any(f["kind"] == "textarea" for f in fillable):
        if BLOCKED.search(text) or len(text) < 400:
            return "captcha"
    if BLOCKED.search(text[:600]) and len(fillable) == 0:
        return "blocked"
    if SUBMITTED.search(text) and not any(b["text"].lower() in ("submit", "submit application") for b in s["buttons"] if not b["disabled"]):
        return "submitted"
    if ALREADY.search(text):
        return "already_applied"
    if CLOSED.search(text) and len(fillable) < 3 and not any(APPLY_WORDS.match(b["text"]) for b in s["buttons"] if not b["disabled"]):
        return "closed"

    # one-time code: a single short input labelled like a code, or autocomplete=one-time-code, or several single-digit boxes
    boxes = [f for f in fillable if f["kind"] in ("text", "number", "code", "tel")]
    if any(f["autocomplete"] == "one-time-code" for f in boxes):
        return "otp"
    if boxes and not pw and len(boxes) <= 8:
        digits = [f for f in boxes if f["maxlength"] == 1]
        if len(digits) >= 4:
            return "otp"
        if len(boxes) == 1 and OTP_LABEL.search(f"{boxes[0]['label']} {boxes[0]['name']} {boxes[0]['placeholder']}") and re.search(r"code|otp|pin|verif", f"{boxes[0]['label']} {boxes[0]['name']} {boxes[0]['placeholder']} {low[:400]}", re.I):
            return "otp"
    if VERIFY_EMAIL.search(text[:1500]) and len(fillable) <= 2 and not pw:
        return "verify_email"

    if pw:
        if len(pw) >= 2 or CREATE_BTN.search(" ".join(h for h in s["headings"]) + " " + " ".join(b["text"] for b in s["buttons"] if not b["link"])):
            if len(pw) >= 2 or re.search(r"create (an )?account|register|sign ?up|new (user|account)", " ".join(s["headings"]).lower() + " " + s["title"].lower()):
                return "create_account"
        return "signin"
    if re.search(r"^(sign ?in|log ?in)\b", " ".join(s["headings"]).lower()) and any(f["kind"] in ("email", "text") for f in fillable) and len(fillable) <= 3:
        return "signin"   # email-first sign-in (password on the next page)

    real = [f for f in fillable if f["kind"] not in ("checkbox",) or f["required"]]
    if len(real) >= 2 or any(f["kind"] in ("textarea", "file", "select", "radio", "combo") for f in fields):
        return "form"
    if any(APPLY_WORDS.match(b["text"]) for b in s["buttons"]):
        return "job_detail"
    if fields:
        return "form"
    if any(FINAL.match(b["text"]) or NEXT.match(b["text"]) for b in s["buttons"]):
        return "form"
    return "unknown"
