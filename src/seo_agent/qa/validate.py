"""QA gate: every label set, recommendation and email must pass before it is used or sent.

Each validator returns a list of error strings; an empty list means it passed.
"""
import re
from urllib.parse import urlsplit

from ..crawl.parser import normalize_url
from ..score.priority import FACTORS, check_scores, priority

REQUIRED_TEXT = ("id", "title", "finding", "impact", "action", "validation")
EVIDENCE_SOURCES = {"crawl", "gsc", "ga4", "psi", "alert"}
LINK = re.compile(r"""https?://[^\s"'<>)\]]+""")
SECRET_MARKERS = ("private_key", "BEGIN PRIVATE KEY", "client_email", "GOOGLE_SA_JSON_B64")


def _nonempty(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_url(url, domain: str, known: set[str]) -> str | None:
    if not isinstance(url, str):
        return f"URL {url!r} is not a string"
    p = urlsplit(url)
    if p.scheme != "https" or not p.netloc:
        return f"URL {url!r} is not an absolute https URL"
    if p.netloc.lower() not in (domain, f"www.{domain}"):
        return f"URL {url!r} is not on {domain}"
    if normalize_url(url) not in known:
        return f"URL {url!r} is not a page the crawl or GSC data knows"
    return None


def validate_labels(labels: dict, weekly: dict, intents_allowed: list[str]) -> list[str]:
    errors = []
    queries = {r["query"] for r in weekly["query_page"]}
    candidates = {c["id"] for c in weekly["candidates"]}
    for q, intent in (labels.get("intents") or {}).items():
        if q not in queries:
            errors.append(f"intent for unknown query {q!r}")
        if intent not in intents_allowed:
            errors.append(f"intent {intent!r} for {q!r} is not one of {intents_allowed}")
    missing = queries - set(labels.get("intents") or {})
    if missing:
        errors.append(f"{len(missing)} queries have no intent label, e.g. {sorted(missing)[:3]}")
    for cid, s in (labels.get("scores") or {}).items():
        if cid not in candidates:
            errors.append(f"scores for unknown candidate {cid!r}")
        errors += [f"{cid}: {e}" for e in check_scores(s)]
        if "priority" in (s or {}):
            errors.append(f"{cid}: do not supply priority; the script computes it")
    unscored = candidates - set(labels.get("scores") or {})
    if unscored:
        errors.append(f"{len(unscored)} candidates have no scores, e.g. {sorted(unscored)[:3]}")
    return errors


def validate_recommendation(rec: dict, weekly: dict, known: set[str], domain: str,
                            approval_levels: list[str]) -> list[str]:
    errors = []
    for f in REQUIRED_TEXT:
        if not _nonempty(rec.get(f)):
            errors.append(f"missing {f}")

    candidates = {c["id"]: c for c in weekly["candidates"]}
    cand = None
    if rec.get("candidate_id") is not None:
        cand = candidates.get(rec["candidate_id"])
        if cand is None:
            errors.append(f"unknown candidate_id {rec['candidate_id']!r}")

    evidence = rec.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("missing evidence: give at least one item")
        evidence = []
    for i, ev in enumerate(evidence):
        if not isinstance(ev, dict) or ev.get("source") not in EVIDENCE_SOURCES:
            errors.append(f"evidence[{i}]: source must be one of {sorted(EVIDENCE_SOURCES)}")
            continue
        if not (_nonempty(ev.get("detail")) or "value" in ev):
            errors.append(f"evidence[{i}]: needs a detail or a metric/value")
        if _is_number(ev.get("value")):
            # Numbers must come from the scripts: match the candidate's evidence exactly.
            metric = ev.get("metric")
            if cand is None or metric not in cand["evidence"]:
                errors.append(f"evidence[{i}]: number {ev.get('value')} for {metric!r} is not traceable "
                              "to a candidate's evidence")
            elif cand["evidence"][metric] != ev["value"]:
                errors.append(f"evidence[{i}]: {metric} is {cand['evidence'][metric]} in weekly.json, "
                              f"not {ev['value']}")

    urls, queries = rec.get("urls") or [], rec.get("queries") or []
    if not urls and not queries:
        errors.append("give at least one URL or query")
    for u in urls:
        e = check_url(u, domain, known)
        if e:
            errors.append(e)
    known_queries = {r["query"] for r in weekly["query_page"]} | {q for c in weekly["candidates"] for q in c["queries"]}
    for q in queries:
        if q not in known_queries:
            errors.append(f"query {q!r} is not in the GSC data")

    steps = rec.get("steps")
    if not isinstance(steps, list) or not steps or not all(_nonempty(s) for s in steps):
        errors.append("steps must be a non-empty list of strings")

    scores = rec.get("scores")
    score_errors = check_scores(scores)
    errors += score_errors
    if not score_errors:
        for f in ("effort", "confidence"):
            if rec.get(f) != scores[f]:
                errors.append(f"{f} must equal scores.{f} ({scores[f]})")
        want = priority(scores)
        if rec.get("priority") != want:
            errors.append(f"priority must be {want} (computed), got {rec.get('priority')!r}")
        if cand and cand.get("scores") and any(cand["scores"][f] != scores[f] for f in FACTORS):
            errors.append("scores differ from the candidate's scores in weekly.json")

    if rec.get("approval_level") not in approval_levels:
        errors.append(f"approval_level must be one of {approval_levels}")
    return errors


def validate_email(email: dict, env, known: set[str], domain: str, passed_ids: set[str],
                   secret: str | None = None) -> list[str]:
    errors = []
    kind = email.get("kind")
    if kind not in ("report", "alert"):
        errors.append("kind must be 'report' or 'alert'")
    allowed = set(env.allowed_recipients(kind or "report"))
    to = email.get("to")
    if not isinstance(to, list) or not to:
        errors.append("to must be a non-empty list")
        to = []
    for addr in to:
        if addr not in allowed:
            errors.append(f"recipient {addr!r} is not allowed in {env.delivery_mode} mode")
    for f in ("cc", "bcc"):
        if email.get(f):
            errors.append(f"{f} is not allowed")
    subject, body = email.get("subject"), email.get("body")
    if not _nonempty(subject):
        errors.append("missing subject")
    elif env.delivery_mode == "shadow" and not subject.startswith("[SHADOW]"):
        errors.append("shadow-mode subject must start with [SHADOW]")
    if not _nonempty(body):
        errors.append("missing body")
        body = ""
    text = f"{subject or ''}\n{body}"
    if any(m in text for m in SECRET_MARKERS) or (secret and secret[:24] in text):
        errors.append("email contains credential material")
    for link in LINK.findall(body):
        link = link.rstrip(".,;:")
        if link.startswith("http://"):
            errors.append(f"link {link!r} is not https")
        elif urlsplit(link).netloc.lower() in (domain, f"www.{domain}"):
            e = check_url(link, domain, known)
            if e:
                errors.append(e)
    for rid in email.get("recommendation_ids") or []:
        if rid not in passed_ids:
            errors.append(f"recommendation {rid!r} has not passed qa")
    return errors
