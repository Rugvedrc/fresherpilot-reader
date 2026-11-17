"""Doing things on the page with real input (Playwright sends trusted mouse / keyboard events), with fallbacks for stubborn widgets."""
from __future__ import annotations

import re
from typing import Any

from .answerbank import alias_match
from .snap import locate

OPTION_SEL = ("[role=option], [role=listbox] li, ul[role=listbox] > *, [data-automation-id=promptOption], [data-automation-id*=menuItem i], "
              ".select2-results__option, .Select-option, [class*=option i][role], li[class*=option i], div[class*=menu] [class*=option i], mat-option, .ant-select-item-option, .dropdown-menu li")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#.%@ ]+", " ", (t or "").lower())).strip()


def _scroll(loc) -> None:
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
    except Exception:  # noqa: BLE001
        pass


def click(page, loc) -> bool:
    """Real click; scrolls into view, waits for it to be enabled, falls back to force and then to a DOM click."""
    _scroll(loc)
    for attempt in (0, 1, 2):
        try:
            if attempt == 0:
                loc.click(timeout=4000)
            elif attempt == 1:
                loc.click(timeout=3000, force=True)
            else:
                loc.evaluate("e => e.click()")
            return True
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(200)
    return False


def _read(loc) -> str:
    try:
        return (loc.input_value(timeout=1500) or "").strip()
    except Exception:  # noqa: BLE001
        try:
            return (loc.inner_text(timeout=1500) or "").strip()
        except Exception:  # noqa: BLE001
            return ""


def set_text(page, loc, value: str) -> bool:
    """fill() first; if the page did not keep the value (masked / React inputs) retype it key by key; last resort: events through JS."""
    _scroll(loc)
    for attempt in (0, 1, 2):
        try:
            if attempt == 0:
                loc.fill(value, timeout=4000)
            elif attempt == 1:
                loc.click(timeout=3000)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                loc.press_sequentially(value, delay=12, timeout=8000)
            else:
                loc.evaluate("""(e, v) => { const d = Object.getOwnPropertyDescriptor(e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value');
                    e.focus(); d.set.call(e, v); e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); e.blur(); }""", value)
            got = _read(loc)
            if _norm(got) == _norm(value) or (re.sub(r"\D", "", value) and re.sub(r"\D", "", got) == re.sub(r"\D", "", value)):
                try:
                    loc.blur()
                except Exception:  # noqa: BLE001
                    pass
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def set_select(page, loc, value: str) -> bool:
    _scroll(loc)
    for kw in ({"label": value}, {"value": value}):
        try:
            loc.select_option(timeout=3000, **kw)
            return True
        except Exception:  # noqa: BLE001
            continue
    try:   # case / whitespace differences
        opts = loc.evaluate("e => Array.from(e.options).map(o => [o.value, o.textContent])")
        for v, t in opts:
            if _norm(t) == _norm(value):
                loc.select_option(value=v, timeout=3000)
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def set_checkbox(page, loc, want: bool) -> bool:
    _scroll(loc)
    try:
        loc.set_checked(want, timeout=3000, force=True)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:   # custom checkbox: click it (or its label) until the state matches
        for _ in range(2):
            now = loc.evaluate("e => e.checked === true || e.getAttribute('aria-checked') === 'true'")
            if now == want:
                return True
            click(page, loc)
            page.wait_for_timeout(150)
        return loc.evaluate("e => e.checked === true || e.getAttribute('aria-checked') === 'true'") == want
    except Exception:  # noqa: BLE001
        return False


def choose_radio(page, snap: dict, field: dict[str, Any], value: str) -> bool:
    from .answers import match_option
    opt = match_option(value, field["options"])
    if not opt:
        return False
    i = field["options"].index(opt)
    loc = locate(snap, field["opt_ids"][i])
    _scroll(loc)
    try:
        loc.check(timeout=3000, force=True)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:   # the visible thing is often the label / a styled span
        loc.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e.parentElement).click()")
        return True
    except Exception:  # noqa: BLE001
        return click(page, loc)


def visible_options(page) -> list[tuple[Any, str]]:
    """(locator, text) of the options of the dropdown that is open now."""
    out = []
    try:
        loc = page.locator(OPTION_SEL)
        n = min(loc.count(), 200)
        for i in range(n):
            o = loc.nth(i)
            try:
                if o.is_visible():
                    t = (o.inner_text(timeout=500) or "").strip()
                    if t:
                        out.append((o, t))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return out


def read_combo_options(page, loc) -> list[str]:
    """Open a custom dropdown, read its options, close it (so the answer can be matched to a real option)."""
    try:
        click(page, loc)
        for _ in range(6):
            page.wait_for_timeout(180)
            opts = visible_options(page)
            if opts:
                texts = list(dict.fromkeys(t for _, t in opts))[:120]
                page.keyboard.press("Escape")
                page.wait_for_timeout(120)
                if visible_options(page):   # Escape did not close it (toggle-style dropdown): click the control again
                    click(page, loc)
                    page.wait_for_timeout(150)
                return [t for t in texts if not re.match(r"^(select|choose|please|no results|loading)", t, re.I)]
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    return []


def set_combo(page, loc, value: str, alts: list[str] | None = None) -> bool:
    """Custom dropdown / typeahead: open, (type to filter), click the option that means the same as value."""
    tries = [value] + [a for a in (alts or []) if a != value]
    is_input = False
    try:
        is_input = loc.evaluate("e => e.tagName === 'INPUT' || e.isContentEditable")
    except Exception:  # noqa: BLE001
        pass
    for attempt, v in enumerate(tries[:5]):
        try:
            if not visible_options(page):   # already open (a toggle-style list left open) -> do not click it shut
                click(page, loc)
            if is_input:
                page.keyboard.press("Control+A")
                loc.press_sequentially(v, delay=25, timeout=6000)
            for _ in range(8):
                page.wait_for_timeout(200)
                opts = visible_options(page)
                if opts:
                    texts = [t for _, t in opts]
                    pick = alias_match(v, texts) or next((t for t in texts if _norm(t) == _norm(v)), "")
                    if not pick:
                        pick = next((t for t in texts if _norm(v) in _norm(t)), "")
                    if pick:
                        o = next(o for o, t in opts if t == pick)
                        click(page, o)
                        page.wait_for_timeout(250)
                        return True
                    break
            if is_input and attempt == 0:   # nothing to click: some typeaheads accept Enter on the first hit
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                page.wait_for_timeout(250)
                if _read(loc) and _norm(v) in _norm(_read(loc)):
                    return True
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            continue
    return False


def upload(page, loc, path: str) -> bool:
    try:
        loc.set_input_files(path, timeout=8000)
        page.wait_for_timeout(1200)
        return True
    except Exception:  # noqa: BLE001
        return False


def apply_answer(page, snap: dict, field: dict[str, Any], ans: dict[str, Any]) -> bool:
    k, value = field["kind"], ans["value"]
    if not value:
        return False
    if k == "radio":
        return choose_radio(page, snap, field, value)
    loc = locate(snap, field["id"])
    if k == "checkbox":
        return set_checkbox(page, loc, value == "true")
    if k == "select":
        return set_select(page, loc, value)
    if k == "combo":
        return set_combo(page, loc, value, ans.get("alts"))
    return set_text(page, loc, value)
