"""Minimal robots.txt parsing: Disallow rules for our UA (or *), with Google-style wildcards."""
import re
from urllib.parse import urlsplit


def parse_robots(text: str, user_agent: str = "ArbcentrixSEOAgent") -> dict:
    groups, agents, rules, in_rules = {}, [], [], False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:
                for a in agents:
                    groups.setdefault(a, []).extend(rules)
                agents, rules, in_rules = [], [], False
            agents.append(val.lower())
        elif key in ("allow", "disallow"):
            in_rules = True
            rules.append((key, val))
    for a in agents:
        groups.setdefault(a, []).extend(rules)
    ua = user_agent.lower()
    chosen = next((g for a, g in groups.items() if a != "*" and a in ua), groups.get("*", []))
    return {"allow": [v for k, v in chosen if k == "allow" and v],
            "disallow": [v for k, v in chosen if k == "disallow" and v]}


def _pattern(rule: str) -> re.Pattern:
    anchored = rule.endswith("$")
    body = re.escape(rule.rstrip("$")).replace(r"\*", ".*")
    return re.compile("^" + body + ("$" if anchored else ""))


def is_blocked(robots: dict, url: str) -> bool:
    """Longest matching rule wins; Allow wins ties."""
    parts = urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    best = (-1, "allow")
    for kind in ("disallow", "allow"):
        for rule in robots.get(kind, []):
            if _pattern(rule).match(path):
                n = len(rule)
                if n > best[0] or (n == best[0] and kind == "allow"):
                    best = (n, kind)
    return best[1] == "disallow"
