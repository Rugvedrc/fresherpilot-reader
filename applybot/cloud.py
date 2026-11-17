"""The runner's connection to the Worker (queue, profile, AI, verification codes, results)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any

import requests

URL = os.environ.get("FP_CLOUD_URL", "").rstrip("/")
HEAD = {"X-FP-Secret": os.environ.get("FP_CLOUD_SECRET", ""), "Content-Type": "application/json"}


def _call(method: str, path: str, **kw) -> dict[str, Any]:
    last: Exception | None = None
    for i in range(3):
        try:
            r = requests.request(method, URL + path, headers=HEAD, timeout=40, **kw)
            if r.status_code == 404 and "detail" in (r.json() if r.content else {}):
                return {"_404": True}
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + 3 * i)
    raise RuntimeError(f"cloud {path}: {last}")


def claim() -> dict[str, Any]:
    return _call("GET", "/api/apply/claim")


def file(name: str) -> dict[str, Any]:
    return _call("GET", f"/api/apply/file?name={name}")


def alive(job_id: int) -> None:
    try:
        _call("POST", "/api/apply/alive", json={"id": job_id})
    except Exception:  # noqa: BLE001
        pass


def result(job_id: int, status: str, detail: str, steps: str) -> None:
    _call("POST", "/api/apply/result", json={"id": job_id, "status": status, "detail": detail[:600], "steps": steps[-3500:]})


def account(domain: str, email: str) -> None:
    try:
        _call("POST", "/api/apply/account", json={"domain": domain, "email": email})
    except Exception:  # noqa: BLE001
        pass


def otp_request(job_id: int, kind: str, hint: str, domain: str) -> str:
    return _call("POST", "/api/apply/otp/request", json={"id": job_id, "kind": kind, "hint": hint, "domain": domain})["req"]


def otp_wait(req: str, job_id: int, minutes: float = 10) -> str | None:
    end = time.time() + minutes * 60
    while time.time() < end:
        code = _call("GET", f"/api/apply/otp?req={req}").get("code")
        if code:
            return code
        alive(job_id)
        time.sleep(6)
    return None


def ai(system: str, user: str, schema: dict) -> list | None:
    d = _call("POST", "/api/apply/ai", json={"system": system, "user": user, "schema": schema, "effort": "low"})
    return d.get("r")


def password_for(domain: str) -> str:
    """One password per portal, derived from a secret that only the owner has: nothing to store, and the owner can recompute it
    (scripts/portal_password.py) to log in by hand or to reset it."""
    key = os.environ.get("APPLY_PW_SECRET", "")
    d = hmac.new(key.encode(), domain.lower().encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(d).decode()[:12] + "aA1!"
