import requests

ENDPOINT = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"


def ping(url: str, strategy: str = "mobile") -> str:
    """One call. Auth is injected by the environment proxy; no API key needed."""
    r = requests.get(ENDPOINT, params={"url": url, "strategy": strategy, "category": "performance"},
                     timeout=120)
    r.raise_for_status()
    score = r.json()["lighthouseResult"]["categories"]["performance"]["score"]
    return f"{strategy} performance={score}"
