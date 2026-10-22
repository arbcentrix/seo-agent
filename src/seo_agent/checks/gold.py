"""Gold-file format shared by tests and `python -m seo_agent` tooling: {rule: [urls]}."""
from collections import defaultdict


def findings_to_gold(findings: list[dict]) -> dict:
    out = defaultdict(list)
    for f in findings:
        out[f["rule"]].append(f["url"])
    return {rule: sorted(urls) for rule, urls in sorted(out.items())}
