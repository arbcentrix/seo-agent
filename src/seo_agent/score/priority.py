"""Priority = (Impact x Relevance x Confidence) / (Effort x Risk). Scores are integers 1-5."""

FACTORS = ("impact", "relevance", "confidence", "effort", "risk")


class ScoreError(ValueError):
    pass


def check_scores(scores) -> list[str]:
    if not isinstance(scores, dict):
        return ["scores must be an object with " + ", ".join(FACTORS)]
    errors = []
    for f in FACTORS:
        v = scores.get(f)
        if not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5):
            errors.append(f"scores.{f} must be an integer 1-5, got {v!r}")
    return errors


def priority(scores: dict) -> float:
    errors = check_scores(scores)
    if errors:
        raise ScoreError("; ".join(errors))
    s = scores
    return round(s["impact"] * s["relevance"] * s["confidence"] / (s["effort"] * s["risk"]), 2)
