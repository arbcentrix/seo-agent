# seo-agent
## Setup

```
pip install -r requirements.txt        # pinned package list
PYTHONPATH=src python -m seo_agent smoke
python -m pytest
```

Commands: `python -m seo_agent daily | watch | weekly | monthly | qa | smoke`.

### Environment

| Variable | Use |
|---|---|
| `GOOGLE_SA_JSON_B64` | Base64 service-account JSON for GSC + GA4 (never printed) |
| `GSC_SITE` | GSC property, e.g. `sc-domain:arbcentrix.com` |
| `GA4_PROPERTY_ID` | GA4 property ID |
| `STATE_BRANCH` | Branch holding snapshots and `runs.jsonl` (default `state`) |
| `DELIVERY_MODE` | `shadow` (only `SHADOW_EMAIL`) or `live` (`REPORTS_TO` / `ALERTS_TO`) |
| `MONDAY_BOARD_ID`, `MONDAY_WORKSPACE_ID` | The only monday.com board/workspace the agent may touch |

PSI calls go through the environment proxy, which injects the API key.

### Crawl and checks (stage 2)

```
python -m seo_agent daily                 # live crawl, checks, out/ + state branch
python -m seo_agent daily --no-state      # local run, nothing committed
python -m seo_agent daily --no-state --fixtures tests/fixtures/2026-10-22
```

The crawler reads robots.txt and the sitemap, fetches every sitemap URL and the critical
pages, then follows internal links (max 300 pages). One request per second, user agent
`ArbcentrixSEOAgent/1.0`, redirects recorded hop by hop. Framer renders pages on demand,
so a page whose first response is an empty shell is fetched once more and only reported
(`empty_shell`) if the shell persists.

Checks are listed in `config/rules.yaml` (id, severity, mode, scope, threshold, watch).
`daily` writes `out/snapshot.json` and `out/findings.json` (every check, for reports). `tests/fixtures/2026-10-22`
holds the gzipped HTML of the 59 sitemap pages plus /privacy-policy and /terms;
`tests/gold/discovery_2026-10-22.json` is the expected findings, and
`tests/test_discovery.py` asserts the checks reproduce them exactly.

### Regression diff and alerts (stage 3)

```
python -m seo_agent watch                 # same crawl, alerts only
python -m seo_agent daily --no-state --previous old/snapshot.json   # local diff
```

`daily` and `watch` both write `out/alerts.json`: the new and ongoing alerts from the
`watch: true` rules, `[]` when healthy.

- `mode: state` rules alert whenever they fire (e.g. tracking or CTA missing).
- `mode: diff` rules alert only on a regression: firing now, not on the previous
  snapshot. Long-standing issues (today: brand-only titles on every page) are discovery
  findings, not alerts. The first run only records the baseline.
- An alert stays open, reported as `"status": "ongoing"`, until its rule stops firing;
  then it is logged to `alerts/resolved.jsonl`. Comparative rules (sitemap URL loss,
  `content_shrink`) keep measuring against the value from before the alert opened.
- A page that fails to load keeps its last good parse (`last_ok`), so it is not
  mistaken for a title/noindex regression when it comes back.

State branch files: `snapshots/latest.json` (diff baseline, updated by every run),
`snapshots/<date>.json` (daily), `alerts/open.json`, `alerts/resolved.jsonl`, `runs.jsonl`.

### State branch

`seo_agent.state.StateBranch` checks out the state branch in `.state-worktree/`
(an orphan branch, created on first use) and provides `read_json`/`write_json`,
`append_run` (one line per run in `runs.jsonl`) and `commit_and_push`.
