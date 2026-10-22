# seo-agent
## Setup

```
pip install -r requirements.txt        # pinned package list
PYTHONPATH=src python -m seo_agent smoke
python -m pytest
```

Commands: `python -m seo_agent daily | watch | weekly | monthly | qa | smoke | sync`.

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

### Data sources (stage 4)

```
python -m seo_agent sync                    # GSC + GA4 + PSI into the state branch
python -m seo_agent sync --only gsc,ga4     # one or more sources
python -m seo_agent sync --force-psi        # PSI even if it ran in the last 7 days
python -m seo_agent sync --no-state --out tmp   # into tmp/state-local, nothing pushed
```

`daily` runs the same sync before crawling (skip with `--skip-sources`). GSC and GA4 sync
every missing day in a 16-month window, so the first run is the one-time backfill
(~40 s for GSC today) and later runs fetch new days plus the last few, which Google
still revises. A source that fails is logged in `runs.jsonl`; the others still run.
GSC search queries are untrusted: ones that read like instructions are flagged in the
run's `suspicious` list.

| Path on the state branch | Contents |
|---|---|
| `gsc/page_query/<YYYY-MM>.json` | date × page × query: clicks, impressions, position (final data, up to today − 3) |
| `gsc/page/<YYYY-MM>.json` | date × page totals — use these for page KPIs: query rows omit anonymised queries |
| `ga4/landing/<YYYY-MM>.json` | date × landingPage × channel (`sessionDefaultChannelGroup`): sessions, engagedSessions, totalUsers, keyEvents |
| `ga4/key_events/<YYYY-MM>.json` | date × landingPage × channel × eventName: keyEvents |
| `psi/<YYYY-MM-DD>.json` | critical pages × mobile/desktop: lab (performance, LCP, FCP, TBT, SI, CLS) and field CrUX (page and origin) |

Each month file lists the days it has synced (`days`), including days with no rows.
Settings are under `sources:` in `config/site.yaml`.

### Weekly report, scoring and QA (stage 5)

```
python -m seo_agent weekly                          # sync sources, write out/weekly.json
python -m seo_agent qa --labels out/labels.json     # Claude's intents + scores
python -m seo_agent weekly --labels out/labels.json # apply them: Priority computed, candidates ranked
python -m seo_agent qa --recommendations out/recommendations.json --email out/email.json
```

`out/weekly.json` holds, all computed by scripts:
- `kpis`: GSC clicks, impressions, CTR, impression-weighted position, and GA4 sessions,
  engaged sessions and key events (organic and all channels), for 7 days vs the previous
  7, 28 vs the previous 28, and 28 vs the same 28 days last year. Windows end on the
  latest day of final GSC data.
- `changes`: page and query movers over 28 days (gainers, losers, new, lost).
- `query_page`: every query × page pair over 28 days, with `branded` (from
  `brand_terms` in site.yaml) and an empty `intent` for Claude.
- `candidates`: one per crawl rule with findings, one per open alert, and the data
  candidates in `rules.yaml` (`striking_distance`, `low_ctr`, `page_decline`,
  `cannibalisation`, `critical_no_impressions`, `psi_poor`), each with its evidence and
  empty `scores`.
- `psi` (latest run) and `site_health`.

Claude writes a labels file: `{"intents": {query: intent}, "scores": {candidate_id:
{impact, relevance, confidence, effort, risk, rationale}}}`, every score an integer 1–5.
`weekly --labels` computes **Priority = (Impact × Relevance × Confidence) / (Effort × Risk)**
and sorts the candidates by it.

`qa` rejects:
- labels with an unknown intent or query, a missing or out-of-range score, or a priority
  Claude supplied itself;
- recommendations missing any of finding, evidence, URLs/queries, impact, action, steps,
  effort, confidence, validation criteria and approval level; with an invalid URL
  (not https, not on arbcentrix.com, or unknown to the crawl and GSC data); with a query
  not in the GSC data; with a number that does not match the candidate's evidence in
  weekly.json; or with a priority that is not the computed one;
- emails to anyone outside the allowed recipients for the delivery mode, with cc/bcc, a
  shadow-mode subject not starting with `[SHADOW]`, credential material, non-https or
  unknown arbcentrix.com links, or a recommendation that has not passed qa.

`qa` exits 0 when everything passed, 1 when anything was rejected (details in
`out/qa.json`), 2 when there was nothing to check.

### State branch

`seo_agent.state.StateBranch` checks out the state branch in `.state-worktree/`
(an orphan branch, created on first use) and provides `read_json`/`write_json`,
`append_run` (one line per run in `runs.jsonl`) and `commit_and_push`.
