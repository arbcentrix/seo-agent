"""PageSpeed Insights: lab (Lighthouse) and field (CrUX) metrics. The proxy injects the key."""
from .google_api import request

ENDPOINT = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"
LAB = {"lcp_ms": "largest-contentful-paint", "fcp_ms": "first-contentful-paint",
       "tbt_ms": "total-blocking-time", "si_ms": "speed-index", "cls": "cumulative-layout-shift"}
FIELD = {"lcp_ms": "LARGEST_CONTENTFUL_PAINT_MS", "inp_ms": "INTERACTION_TO_NEXT_PAINT",
         "cls": "CUMULATIVE_LAYOUT_SHIFT_SCORE", "fcp_ms": "FIRST_CONTENTFUL_PAINT_MS",
         "ttfb_ms": "EXPERIMENTAL_TIME_TO_FIRST_BYTE"}


def run(url: str, strategy: str) -> dict:
    return request("GET", ENDPOINT, timeout=180,
                   params={"url": url, "strategy": strategy, "category": "performance"})


def _field(exp: dict | None) -> dict | None:
    if not exp or not exp.get("metrics"):
        return None
    out = {"category": exp.get("overall_category")}
    for key, name in FIELD.items():
        m = exp["metrics"].get(name)
        if m:
            value = m.get("percentile")
            out[key] = value / 100 if key == "cls" and value is not None else value
            out[f"{key}_category"] = m.get("category")
    return out


def summarize(raw: dict) -> dict:
    lh = raw.get("lighthouseResult", {})
    audits = lh.get("audits", {})
    score = lh.get("categories", {}).get("performance", {}).get("score")
    lab = {"performance": round(score * 100) if score is not None else None}
    for key, audit in LAB.items():
        v = audits.get(audit, {}).get("numericValue")
        lab[key] = round(v, 3) if key == "cls" and v is not None else (round(v) if v is not None else None)
    return {"lab": lab, "field": _field(raw.get("loadingExperience")),
            "origin_field": _field(raw.get("originLoadingExperience")),
            "lighthouse_version": lh.get("lighthouseVersion")}


def ping(url: str, strategy: str = "mobile") -> str:
    return f"{strategy} performance={summarize(run(url, strategy))['lab']['performance']}"
