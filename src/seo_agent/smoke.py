"""python -m seo_agent smoke: one call each to GSC, GA4, PSI and the site."""
from .config import Config
from .http import session
from .sources import ga4, google_auth, gsc, psi


def _run(name, fn):
    try:
        detail = fn()
        return name, True, f"OK {detail}".strip()
    except Exception as e:
        return name, False, f"ERROR {type(e).__name__}: {str(e)[:200]}"


def run_smoke(cfg: Config, out=print) -> bool:
    env = cfg.env
    token_cache = {}

    def token():
        if "t" not in token_cache:
            token_cache["t"] = google_auth.get_token(env.google_sa_json_b64)
        return token_cache["t"]

    def site():
        r = session(cfg.user_agent).get(cfg.base_url + "/", timeout=30)
        r.raise_for_status()
        return f"status={r.status_code}"

    checks = [
        ("gsc", lambda: gsc.ping(token(), env.gsc_site)),
        ("ga4", lambda: ga4.ping(token(), env.ga4_property_id)),
        ("psi", lambda: psi.ping(cfg.base_url + "/")),
        ("site", site),
    ]
    results = [_run(n, f) for n, f in checks]
    for name, _, msg in results:
        out(f"{name}: {msg}")
    return all(ok for _, ok, _ in results)
