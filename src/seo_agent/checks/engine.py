"""Run the checks listed in rules.yaml over a crawl snapshot.

Each check is f(snapshot, ctx) -> list of (url, evidence) tuples. The engine adds rule id,
severity and ordering. Comparative checks read ctx.previous and ctx.baseline (the evidence
stored when an alert on that URL opened); see regression.py for how alerts are derived.
"""
from dataclasses import dataclass

from . import technical, onpage

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
REGISTRY = {**technical.CHECKS, **onpage.CHECKS}


class RulesError(Exception):
    pass


@dataclass
class Ctx:
    cfg: object
    rule: dict
    previous: dict | None = None
    baseline: dict | None = None  # {url: evidence} of open alerts for this rule

    @property
    def threshold(self) -> dict:
        return self.rule.get("threshold") or {}

    def in_scope(self, url: str) -> bool:
        return self.rule.get("scope", "all") == "all" or url in self.cfg.critical_urls


def validate_rules(rules: dict) -> list[dict]:
    checks = rules.get("checks") or []
    seen = set()
    for r in checks:
        rid = r.get("id")
        if rid not in REGISTRY:
            raise RulesError(f"rules.yaml: unknown check id {rid!r}")
        if rid in seen:
            raise RulesError(f"rules.yaml: duplicate check id {rid!r}")
        if r.get("severity") not in SEVERITY_ORDER:
            raise RulesError(f"rules.yaml: {rid}: bad severity {r.get('severity')!r}")
        seen.add(rid)
    return checks


def active_rules(cfg, only_watch: bool = False) -> list[dict]:
    return [r for r in validate_rules(cfg.rules)
            if r.get("enabled", True) is not False and (r.get("watch") or not only_watch)]


def run_rule(rule: dict, snapshot: dict, cfg, previous=None, baseline=None) -> dict:
    """{url: evidence} for one rule, after its exclude list."""
    ctx = Ctx(cfg, rule, previous, baseline or {})
    excluded = {cfg.base_url + path for path in rule.get("exclude") or []}
    out = {}
    for url, evidence in REGISTRY[rule["id"]](snapshot, ctx):
        if url not in excluded:
            out.setdefault(url, evidence)
    return out


def sort_findings(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER[f["severity"]], f["rule"], f["url"] or ""))


def run_checks(snapshot: dict, cfg, previous: dict | None = None, only_watch: bool = False) -> list[dict]:
    findings = []
    for rule in active_rules(cfg, only_watch):
        for url, evidence in run_rule(rule, snapshot, cfg, previous).items():
            findings.append({"rule": rule["id"], "severity": rule["severity"], "url": url,
                             "evidence": evidence})
    return sort_findings(findings)
