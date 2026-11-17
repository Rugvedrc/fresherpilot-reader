"""Answer bank: the standard questions job forms keep asking, answered from the profile without the AI.

Two parts:
  * bank_answer(field, profile): label/question wording (many aliases per question) -> the profile's answer.
    Works for long question labels too ("Are you legally authorized to work in India?").
  * alias_match(value, options): picks the dropdown/radio option that means the same thing
    ("India" = "IN" = "India (+91)" = "+91"; "Karnataka" = "KA"; "B.E." = "Bachelor of Engineering"; "Bangalore" = "Bengaluru").
An answer the bank is not sure about is still returned but flagged for review. Anything the bank does not know goes to the AI.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable


def _n(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#.%@ ]+", " ", (text or "").lower())).strip()


# ------------------------------------------------------------------ option aliases
# Each group = words that mean the same thing in a form option. Matching is on normalized text.
_GROUPS: list[list[str]] = [
    ["india", "in", "ind", "republic of india", "bharat", "indian", "+91", "91", "in +91", "india +91", "+91 india", "india 91"],
    ["indian citizen", "citizen of india", "i am a citizen of india", "citizen", "indian national", "india citizen"],
    ["male", "m", "man", "mr", "mr.", "he him", "he/him"],
    ["yes", "y", "true", "yes i am", "yes i do", "yes i have", "i am", "i do", "i have", "agree"],
    ["no", "n", "false", "no i am not", "no i do not", "no i don t", "i am not", "i do not", "i dont", "none", "not applicable", "n/a", "na"],
    ["mobile", "cell", "cell phone", "mobile phone", "personal mobile", "phone mobile"],
    ["full time", "full-time", "permanent", "fulltime", "regular", "regular full time", "full time employee"],
    ["bachelor s degree", "bachelors", "bachelor", "bachelor of engineering", "bachelor of technology", "b.e", "be", "b.e.", "b.tech", "btech",
     "b tech", "undergraduate", "ug", "graduate", "graduation", "bachelor degree", "engineering degree", "bachelor s", "bachelors degree"],
    ["information technology", "it", "b.e. it", "be it", "b.e. information technology", "computer science and information technology"],
    ["bangalore", "bengaluru", "bengaluru karnataka", "bangalore karnataka", "blr"],
    ["mumbai", "bombay", "navi mumbai"],
    ["chhatrapati sambhajinagar", "aurangabad", "sambhajinagar", "aurangabad maharashtra"],
    ["pune", "pimpri chinchwad", "pcmc"],
    ["hyderabad", "secunderabad", "hyd"],
    ["job portal", "job board", "online job portal", "job site", "jobs portal", "linkedin", "internet", "online", "website",
     "company website", "career site", "careers page", "other", "job search website", "job website", "social media"],
    ["fresher", "no experience", "0", "0 years", "0 year", "less than 1 year", "less than 1", "0-1 years", "0 1 years", "less than one year", "student",
     "recent graduate", "new graduate", "graduate student", "entry level", "entry-level"],
    ["immediate", "immediately", "0 days", "0", "right away", "asap", "now", "available immediately", "immediate joiner", "less than 15 days"],
    ["not a protected veteran", "i am not a protected veteran", "no i am not a protected veteran", "i am not a veteran", "not a veteran", "no"],
    ["prefer not to say", "decline to self identify", "i do not wish to answer", "choose not to disclose", "prefer not to disclose"],
    ["english", "eng"],
    ["linkedin", "linked in", "linkedin.com"],
    ["asian", "asian indian", "south asian", "asian (indian)", "indian", "asian india"],
    ["general", "gen", "open", "unreserved", "ur", "general category", "open category"],
]

_STATES = {
    "andhra pradesh": "AP", "arunachal pradesh": "AR", "assam": "AS", "bihar": "BR", "chhattisgarh": "CG", "goa": "GA", "gujarat": "GJ",
    "haryana": "HR", "himachal pradesh": "HP", "jharkhand": "JH", "karnataka": "KA", "kerala": "KL", "madhya pradesh": "MP",
    "maharashtra": "MH", "manipur": "MN", "meghalaya": "ML", "mizoram": "MZ", "nagaland": "NL", "odisha": "OD", "punjab": "PB",
    "rajasthan": "RJ", "sikkim": "SK", "tamil nadu": "TN", "telangana": "TS", "tripura": "TR", "uttar pradesh": "UP",
    "uttarakhand": "UK", "west bengal": "WB", "delhi": "DL", "jammu and kashmir": "JK", "ladakh": "LA", "puducherry": "PY",
    "chandigarh": "CH",
}
for _name, _abbr in _STATES.items():
    _GROUPS.append([_name, _abbr.lower(), f"in-{_abbr.lower()}", f"{_name} india", f"{_abbr.lower()} india"])
_GROUPS.append(["telangana", "tg"])
_GROUPS.append(["delhi", "new delhi", "nct of delhi", "national capital territory of delhi"])

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
for _i, _m in enumerate(_MONTHS, 1):
    _GROUPS.append([_m, _m[:3], str(_i), f"{_i:02d}"])

_ALIAS: dict[str, list[str]] = {}
for _g in _GROUPS:
    _ng = [_n(x) for x in _g]
    for _x in _ng:
        _ALIAS.setdefault(_x, [])
        for _y in _ng:
            if _y not in _ALIAS[_x]:
                _ALIAS[_x].append(_y)

# These words are too generic to be matched loosely against long option texts (only exact / whole-option equality).
_STRICT_ONLY = {"in", "it", "be", "m", "n", "y", "na", "0", "ug", "gen", "ur", "now", "i am", "i do", "i have", "other", "online", "open",
                "regular", "graduate", "graduation", "no", "yes", "none", "true", "false", "agree"}


def alias_match(value: str, options: list[str]) -> str:
    """The option equivalent to value through the alias groups ("" when none)."""
    v = _n(value)
    if not v or not options:
        return ""
    norm = {o: _n(o) for o in options if str(o).strip()}
    alts = [v] + [a for a in _ALIAS.get(v, []) if a != v]
    # whole-option equality first
    for a in alts:
        for o, n in norm.items():
            if n == a:
                return o
    # option text that contains the alias as whole words ("India (+91)" holds "india"), longest alias first
    for a in sorted(alts, key=len, reverse=True):
        if a in _STRICT_ONLY or len(a) < 3:
            continue
        for o, n in norm.items():
            if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", n):
                return o
    return ""


def alts(value: str) -> list[str]:
    """Other wordings of the same answer (for a typeahead dropdown whose options the page does not show up front)."""
    out: list[str] = []
    for a in _ALIAS.get(_n(value), []):
        if a != _n(value) and a not in _STRICT_ONLY and len(a) >= 2 and a not in out:
            out.append(a)
    return out[:6]


# ------------------------------------------------------------------ question rules
_FOREIGN = re.compile(
    r"\b(united states|u\.?s\.?a?\b|usa|america|united kingdom|u\.?k\b|uk\b|britain|england|europe|european|eu\b|canada|canadian|australia|new zealand|"
    r"singapore|uae|dubai|emirates|saudi|qatar|germany|france|netherlands|ireland|japan|china|malaysia|philippines|mexico|brazil|poland|romania|"
    r"israel|south africa|hong kong|switzerland|sweden|spain|italy)\b", re.I)

Rule = tuple[re.Pattern[str], Callable[[dict[str, Any], dict[str, Any], str], Any], bool]  # (pattern, answer fn, needs review)
RULES: list[Rule] = []


def _r(pattern: str, fn: Callable[[dict[str, Any], dict[str, Any], str], Any], review: bool = False) -> None:
    RULES.append((re.compile(pattern, re.I), fn, review))


def _const(value: str) -> Callable[[dict[str, Any], dict[str, Any], str], Any]:
    return lambda p, f, t: value


def _p(*path: str, default: str = "") -> Callable[[dict[str, Any], dict[str, Any], str], Any]:
    def get(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
        cur: Any = p
        for k in path:
            cur = (cur or {}).get(k) if isinstance(cur, dict) else None
        return str(cur) if cur not in (None, "") else default
    return get


def _edu(kind: str) -> Callable[[dict[str, Any], dict[str, Any], str], Any]:
    def get(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
        rows = p.get("education") or []
        def find(rx: str) -> dict[str, Any]:
            return next((e for e in rows if re.search(rx, (e.get("degree") or "") , re.I)), {})
        if kind in ("10", "tenth"):
            e = find(r"\bX\b|10")
            return e.get("percentage") or ""
        if kind in ("12", "twelfth"):
            e = find(r"XII|12")
            return e.get("percentage") or ""
        e = rows[0] if rows else {}
        if kind == "cgpa":
            return e.get("cgpa") or ""
        if kind == "scale":
            return e.get("cgpa_scale") or "10"
        if kind == "pct":  # graduation percentage: given, else CGPA x 9.5 (common university conversion)
            if e.get("percentage"):
                return e["percentage"]
            try:
                return f"{float(e.get('cgpa')) * 9.5:.2f}"
            except (TypeError, ValueError):
                return ""
        return e.get(kind) or ""
    return get


def _age(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
    try:
        y, m, d = (int(x) for x in str(p["personal"]["date_of_birth"]).split("-"))
        today = date.today()
        return str(today.year - y - ((today.month, today.day) < (m, d)))
    except (KeyError, ValueError):
        return ""


def _date(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
    iso = str(p["personal"].get("date_of_birth") or "")
    try:
        y, m, d = iso.split("-")
    except ValueError:
        return iso
    hint = f"{f.get('placeholder', '')} {f.get('hint', '')} {f.get('label', '')}".lower()
    if f.get("type") == "date":
        return iso
    if re.search(r"mm\s*[/.-]\s*dd", hint):
        return f"{m}/{d}/{y}"
    if re.search(r"dd\s*[/-]\s*mm|dd/mm", hint):
        return f"{d}/{m}/{y}"
    if re.search(r"dd\s*\.\s*mm", hint):
        return f"{d}.{m}.{y}"
    if re.search(r"yyyy\s*-\s*mm", hint):
        return iso
    if re.search(r"dd\s*-\s*mmm|dd mmm", hint):
        return f"{d}-{_MONTHS[int(m) - 1][:3].title()}-{y}"
    return f"{d}/{m}/{y}"


def _salutation(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
    return "Mr" if (p["personal"].get("gender") or "").lower().startswith("m") else ("Ms" if (p["personal"].get("gender") or "").lower().startswith("f") else "")


def _address_line(n: int) -> Callable[[dict[str, Any], dict[str, Any], str], Any]:
    def get(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
        pe = p["personal"]
        if n == 1:
            return pe.get("address") or pe.get("city") or ""
        return ""
    return get


def _initials(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
    pe = p["personal"]
    return "".join(x[0].upper() for x in (pe.get("first_name"), pe.get("middle_name"), pe.get("last_name")) if x)


def _middle_initial(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
    m = p["personal"].get("middle_name") or ""
    return m[:1].upper()


def _full_with_middle(p: dict[str, Any], f: dict[str, Any], t: str) -> Any:
    return p["personal"].get("full_name") or ""


def _no_foreign(fn: Callable[[dict[str, Any], dict[str, Any], str], Any]) -> Callable[[dict[str, Any], dict[str, Any], str], Any]:
    """Authorization/sponsorship/relocation questions about another country are not answered here (the AI + you decide)."""
    return lambda p, f, t: None if _FOREIGN.search(t) else fn(p, f, t)


# --- identity / contact (many wordings per field)
_r(r"\b(first|given|fore) ?name\b|^name \(?first\)?|^preferred (first )?name|^nickname|^name you (go|like to be called) by|^what should we call you", _p("personal", "first_name"))
_r(r"\bmiddle (name|initial)\b", lambda p, f, t: _middle_initial(p, f, t) if re.search(r"initial", t) else p["personal"].get("middle_name", ""))
_r(r"\b(last|family|sur) ?name\b|^name \(?last\)?|^second name", _p("personal", "last_name"))
_r(r"\binitials?\b", _initials)
_r(r"^(salutation|title|prefix|mr/?ms|honorific)$|^(salutation|title|prefix)\b", _salutation)
_r(r"^(your |candidate |applicant |legal |complete |full )*(full )?name( as (per|on|in) .*)?$|^full name|^legal name|^name of (the )?(candidate|applicant)|^candidate name|^applicant name|^name as per|^your name$|^name$|^complete name",
   _full_with_middle)
_r(r"e-?mail|mail id|email id|^mail$", _p("personal", "email"))
_r(r"(phone|mobile|contact|whatsapp|cell)[ -]?(number|no\b|num\b|#)?\b.*(country|dial|isd|std) ?code|^(country|dial|isd|std) ?code|^calling code|^phone code", _p("personal", "phone_country_code"))
_r(r"(phone|mobile|contact) ?(type|kind)\b|^type of (phone|number)", _const("Mobile"))
_r(r"^(alternate|alternative|secondary|emergency|parent|guardian|father|mother)\b.*(phone|mobile|contact|number)", _const(""))
_r(r"\b(phone|mobile|contact number|cell|whatsapp|telephone)\b", _p("personal", "phone"))
_r(r"linked ?in", _p("links", "linkedin"))
_r(r"git ?hub", _p("links", "github"))
_r(r"portfolio|personal (web)?site|^website|\bblog\b|other (url|link|website)|^url$|^link$|personal url", _p("links", "portfolio"))
_r(r"leet ?code|hacker ?rank|code ?forces|code ?chef", _p("links", "leetcode"))
_r(r"date of birth|\bdob\b|birth ?date|born on|d\.o\.b", _date)
_r(r"^age\b|your age|age in years|how old are you", _age)
_r(r"^(gender|sex)\b|gender identity|^what is your gender|^your gender", _p("diversity", "gender"))
_r(r"(are you|do you) (at least|over|above|older than) (18|eighteen)|18 years (of age )?or older|legal (working )?age|minimum age", _const("Yes"))
_r(r"^nationality|^citizenship$|country of citizenship|citizen of which|^citizen$|which country are you a citizen|^country of (nationality|origin)", _p("personal", "nationality", default="India"))
_r(r"^place of birth|^birth ?place|^city of birth", _p("personal", "city"))
_r(r"^country of (residence)?$|^country( / region)?$|^country name$|country you (live|reside) in|^country of residence|current country|which country do you (live|reside)|^residing country|^resident country", _p("personal", "country"))
_r(r"^(current |your |home )?(state|province|region)( name| of residence)?$|^state of residence|state you (live|reside)", _p("personal", "state"))
_r(r"^(current |present |home )?(city|town)( of residence)?$|^city name|current city|city of residence|your city|which city (do|are)|^(current )?location$|^current location|where are you (currently )?(based|located|staying)|^present location|^home town|^hometown", _p("personal", "city"))
_r(r"pin ?code|postal ?code|post code|zip ?(code)?|\bpin\b", _p("personal", "pincode"))
_r(r"^address (line )?(1|one)\b|^street( address)?$|^address$|^current address|^present address|^residential address|^permanent address|^house|^address line$", _address_line(1))
_r(r"^address (line )?(2|two)\b|^apartment|^suite|^landmark", _address_line(2))

# --- work authorization / sponsorship / location preferences
_r(r"(sponsor|visa|work permit|employment pass|h-?1b|immigration)", _no_foreign(_const("No")))
_r(r"(legally |lawfully )?(authori[sz]ed|eligible|permitted|entitled|allowed|right|legal right) .{0,25}work|work .{0,25}(authori[sz]ation|permit|eligib)|right to work|legally able to work",
   _no_foreign(_p("job_preferences", "authorized_to_work_in_india", default="Yes")))
_r(r"(willing|open|ready|happy|comfortable|able|fine|prepared|agree) .{0,15}(to )?(relocat|move|shift to)|relocation|ready to relocate|can you relocate",
   _no_foreign(_p("job_preferences", "willing_to_relocate", default="Yes")))
_r(r"(willing|open|ready|happy|comfortable|able|fine|prepared) .{0,20}(work|working).{0,15}(from )?(the )?(office|on-?site|onsite|hybrid|in[- ]person)|(work from|working from) (the )?office|on-?site (role|work)|hybrid (work|model|role)|\b(wfo|wfh)\b",
   _no_foreign(_const("Yes")))
_r(r"(willing|open|ready|comfortable|able) .{0,20}(remote|work from home|wfh)|work from home|remote (work|role|position|job)", _no_foreign(_const("Yes")))
_r(r"preferred (work )?(location|city)|location preference|(desired|preferred) (job |work )?location|where would you like to work",
   _p("job_preferences", "preferred_locations"))
_r(r"(work|working) (mode|model|arrangement|preference|type)|preferred (work )?mode", _p("job_preferences", "work_mode", default="Hybrid"))
_r(r"(willing|open|ready|able) .{0,20}(travel)", _no_foreign(_const("Yes")))
_r(r"(willing|open|ready|able|comfortable) .{0,25}(shift|night|rotational|24 ?x ?7|weekend)", _const("Yes"))

# --- job situation
_r(r"notice period|joining time|how (soon|quickly|fast) can you join|when can you (join|start)|earliest (start|joining|date)|availability to join|available to join|time to join|joining (date|availability)|date of joining|(start|available) (date|from)",
   _p("job_preferences", "notice_period", default="Immediate"))
_r(r"(currently|presently) (employed|working)|are you (employed|working)|employment status|current (employment|work) status|are you a (working )?professional", _const("No"))
_r(r"are you (a )?(fresher|fresh graduate|recent graduate|new graduate|student)|fresher or experienced|experience level|candidate type|applicant type|work status|experienced or fresher", _const("Fresher"))
_r(r"(currently|presently) (studying|pursuing|enrolled)|are you (still )?(a )?student|currently a student", _const("No"))
_r(r"current (company|employer|organi[sz]ation)|present (company|employer)|last (company|employer)|most recent (company|employer)|employer name|name of (your )?(current )?employer", _p("job_preferences", "current_company"))
_r(r"(current|present|last) (job )?(title|designation|role|position)", _const("Fresher"))
_r(r"current (ctc|salary|compensation|package|pay)|present (ctc|salary)|last drawn|(ctc|salary) (currently|at present)|existing (ctc|salary)|fixed ctc|annual (ctc|salary) (current|present)|what is your current (ctc|salary)",
   lambda p, f, t: "CTC:" + str(p["job_preferences"].get("current_ctc_lpa", "0")))
_r(r"expected (ctc|salary|compensation|package|pay)|salary expectation|(ctc|salary|pay) expectation|desired (ctc|salary|compensation)|expected annual|expected remuneration|how much (do you )?(expect|want)",
   lambda p, f, t: "CTC:" + str(p["job_preferences"].get("expected_ctc_lpa", "")))
_r(r"(total|overall) (years? of )?(work |professional |relevant |industry )?(experience|exp)\b|years of (professional |work |total |relevant )?experience$|how many years of (total |work |professional )?experience|total exp|(work|professional) experience in years",
   _p("job_preferences", "total_experience_years", default="0"))
_r(r"(any )?(previous|prior|past) (work )?experience$|do you have (any )?(prior |previous |work |professional )experience", _const("No"))
_r(r"(previously|ever|formerly) (been )?(employed|worked|work) (by|at|with|for)|(former|ex)[- ]?(employee|intern)|ever been (an )?(employee|employed)|worked (for|at|with) .{0,40} before|currently work(ing)? (for|at)",
   _const("No"))
_r(r"(relative|relatives|family member|friend|referral|referred by|know anyone|acquaintance).{0,60}(work|employ|here|company)|employee referral|referred by (an )?employee|do you have a referral", _const("No"))
_r(r"(non[- ]?compete|notice served|serving notice|bond|service agreement|contractual obligation)", _const("No"))
_r(r"how did you (hear|come to know|find|learn|get to know)|source of (application|hire|referral|information)|where did you (hear|find|see|learn)|^source$|hear about (us|this|the)|referral source|job source",
   _p("custom_answers", "How did you hear about us?", default="Job portal"))
_r(r"(convicted|conviction|criminal|felony|misdemeanou?r|pending (case|charges)|arrest)", _const("No"))
_r(r"^(?!.*(programming|coding|code|framework|tech|skill|stack)).*(languages? (known|you speak|spoken|proficien)|spoken languages|mother tongue|^languages?$)", _const("English, Hindi, Marathi"))
_r(r"\benglish\b.*(proficien|level|fluen)|(proficien|level|fluen).*\benglish\b", _const("Fluent / Professional"))

# --- education
_r(r"^(highest )?(level of )?(education|qualification|degree)( level| completed| attained)?$|highest (level of )?(education|qualification|degree)|education level|qualification level|^qualification$|^degree( type| level)?$|^education$|level of education|^degree name",
   lambda p, f, t: "Bachelor's degree")
_r(r"^(field of study|branch|specialization|specialisation|major|stream|discipline|area of study)( of study| name)?$|^(course|program|programme)( name| of study)?$|^degree (specialization|branch|field)|branch of (study|engineering)|^what (did you|are you) study|^subject$",
   _edu("field"))
_r(r"(college|university|institute|institution|school) (name|attended)|^(college|university|institution|institute)( name)?$|name of (the )?(college|university|institute|institution)|where (did|do) you (study|graduate)|alma mater|^education (institution|institute)|^school$",
   _edu("institution"))
_r(r"(year of|graduation|passing|passout|pass out|completion|degree completion|graduating|end) (year|date)|year of (passing|graduation|passout|completion)|when (did|will) you graduate|graduat(ed|ing|ion) in|batch( year)?$|^batch\b(?! size)|expected graduation|passing out",
   _p("job_preferences", "graduation_year", default="2026"))
_r(r"(start|joining|admission|enrol?lment|from) (year|date)\b.*(degree|college|graduation|education)|(degree|college|graduation|education).*(start|from) (year|date)", _const("2022"))
_r(r"\b(10th|tenth|ssc|x )\b.*(percent|%|marks|score|cgpa)|(percent|%|marks|score|cgpa).*\b(10th|tenth|ssc|std x)\b|class 10|class x\b|matric", _edu("10"))
_r(r"\b(12th|twelfth|hsc|xii|intermediate|higher secondary)\b.*(percent|%|marks|score|cgpa)|(percent|%|marks|score|cgpa).*\b(12th|twelfth|hsc|xii|intermediate)\b|class 12|class xii|plus two|\+2", _edu("12"))
_r(r"\b(cgpa|gpa|cpi|spi|grade point)\b(?!.*(scale|out of|maximum|max))|graduation (cgpa|gpa)|degree (cgpa|gpa)|current (cgpa|gpa)|aggregate cgpa", _edu("cgpa"))
_r(r"(cgpa|gpa|cpi) (scale|out of|maximum|max)|scale of (cgpa|gpa)|(maximum|max) (cgpa|gpa)|out of how much", _edu("scale"))
_r(r"(graduation|degree|bachelor|b\.?e|b\.?tech|ug|under ?graduate|engineering|college|aggregate|overall|final|current).{0,20}(percentage|percent|%|marks|aggregate)|^(percentage|aggregate|overall percentage|aggregate percentage|marks)$",
   _edu("pct"))
_r(r"(active )?(backlog|arrear|atkt|kt)s?|(any )?(current|active|live) (backlog|arrear)|year ?gap|education gap|gap in (your )?(education|studies)", _const("No"))

REVIEW_ONLY_EMPTY = True  # rules that return "" are for review (the user's own answer is unknown), never auto-fill


def bank_answer(field: dict[str, Any], profile: dict[str, Any]) -> tuple[str, bool] | None:
    """(value, needs_review) for a standard question, or None when the bank has no rule. Value "" = known question, no answer on file."""
    ftype = field.get("type")
    if ftype in ("checkbox", "file"):
        return None
    label = _n(field.get("label") or "")
    if not label or len(label) > 220:
        return None
    # an essay box is a real question for the AI, except short identity labels
    if ftype == "textarea" and len(label.split()) > 4:
        return None
    text = label
    for rx, fn, review in RULES:
        if rx.search(label):
            try:
                value = fn(profile, field, text)
            except Exception:  # noqa: BLE001
                return None
            if value is None:
                return None  # a rule decided to leave it to the AI (e.g. a foreign-country question)
            value = str(value)
            if not value:
                return ("")
            return (value, review)
    return None
