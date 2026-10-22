"""Run the checks listed in rules.yaml over a crawl snapshot.

Each check is f(snapshot, ctx) -> list of (url, evidence) tuples. The engine adds rule id,
severity and ordering. Checks in mode 'diff' get ctx.previous (None until stage 3 wires it).
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


def run_checks(snapshot: dict, cfg, previous: dict | None = None, only_watch: bool = False) -> list[dict]:
    findings = []
    for rule in validate_rules(cfg.rules):
        if rule.get("enabled", True) is False or (only_watch and not rule.get("watch")):
            continue
        ctx = Ctx(cfg, rule, previous)
        excluded = {cfg.base_url + path for path in rule.get("exclude") or []}
        for url, evidence in REGISTRY[rule["id"]](snapshot, ctx):
            if url in excluded:
                continue
            findings.append({"rule": rule["id"], "severity": rule["severity"], "url": url,
                             "evidence": evidence})
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["rule"], f["url"] or ""))
    return findings
