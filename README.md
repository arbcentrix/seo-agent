# seo-agent
## Setup

```
pip install -r requirements.txt        # pinned package list
pip install -e .                       # makes `python -m seo_agent` work from anywhere
python -m seo_agent smoke
python -m pytest
```

The scheduled tasks' environment needs the same two `pip install` lines in its setup
script, and the variables below.

Commands: `python -m seo_agent daily | watch | weekly | monthly | qa | smoke | sync | verify | log`.

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

### Scheduled tasks and prompts (stage 6)

All times are Asia/Manila (UTC+8); when creating the tasks, write each cron as
`CRON_TZ=Asia/Manila <cron>`.

| Task name | Prompt | Model | Trigger | Connectors |
|---|---|---|---|---|
| SEO daily watch | Follow prompts/daily.md in the repository exactly. | Sonnet 5.5 | Daily, 06:13 | Microsoft 365, monday.com |
| SEO publish watch | Follow prompts/watch.md in the repository exactly. | Sonnet 5.5 | Every 4 hours at :27 (cron `27 */4 * * *`), plus an API trigger | Microsoft 365, monday.com |
| SEO weekly report | Follow prompts/weekly.md in the repository exactly. | Sonnet 5.5 | Weekly, Monday 06:43 | Microsoft 365, monday.com |
| SEO monthly report | Follow prompts/monthly.md in the repository exactly. | Opus 5.5 | 3rd of each month, 07:43 (cron `43 7 3 * *`) | Microsoft 365, monday.com |

Each prompt is a short numbered list in the same shape: run the script, stop early when
there is nothing to report, let Claude add only the words (causes, fixes, labels,
scores, write-ups), run `qa` on every file before it is used, email, add monday.com
items, close the run.

- **`qa <file>`** detects what the file is (alerts, recommendations, labels or email).
  For alerts, Claude adds `cause` and `fix` (3 sentences or fewer together), `effort`,
  `confidence` and `approval_level`; qa rejects an alert whose script fields changed or
  whose text uses a number that is not in that alert's evidence (or in its rule's
  description/threshold in rules.yaml). `daily`/`watch` keep an untouched copy in
  `out/alerts_raw.json` for this.
- **Shadow mode** (`DELIVERY_MODE=shadow`): every email goes only to `SHADOW_EMAIL` (the
  technical owner) with `[SHADOW]` in front of the subject, and qa enforces both.
  monday.com items and docs are still created, named with a `[SHADOW]` prefix.
- **One line per run in `runs.jsonl`**: the script writes it; the prompt's last step,
  `python -m seo_agent log --run <task> --note "..."`, closes that line with what was
  sent and commits. If the script crashed before logging, `log --status error` adds the line.
- **`verify --items`**: answers whether an Applied monday.com item's issue has cleared
  (`resolved`, `still_present`, `too_early` for data candidates whose 28-day window
  still includes days before the change, or `unknown`). Only `resolved` lets the weekly
  run move an item from Applied to Verified.
- **Run count**: `weekly.json` → `runs` counts each task's runs, errors and missing runs
  over the past 7 days against `schedule:` in site.yaml. The weekly email reports it.

### Usage and limits

- Runs draw down the seat's plan usage the same way chat and Claude Code sessions do.
  During shadow mode, check claude.ai/settings/usage once a week.
- This schedule is about 50 runs a week (7 daily, 42 publish watch, 1 weekly, plus the
  monthly and any API-triggered watches), far below the 100-runs-per-hour cap on
  scheduled runs.
- If the seat runs out of usage, runs are rejected unless the Owner has turned on usage
  credits.
- If the GitHub connection lapses, runs are skipped for up to 72 hours, then the task
  turns itself off.
- A green status means only that the session ran, not that the work succeeded. The
  Monday email's run count from `runs.jsonl` is the real check.

### Monthly trends (stage 7)

```
python -m seo_agent monthly          # sync sources, write out/monthly.json
```

Calendar months, complete months only (every day of final GSC data synced), ending on
the last complete month:
- `trend_3m`: last 3 months vs the 3 before and vs the same 3 months last year — GSC
  clicks, impressions, CTR, position; GA4 organic sessions, engaged sessions, key events.
- `trend_12m`: the month series (13 months, so the latest month also has its year-ago
  month), totals and a least-squares trend per month. A 12 vs previous-12 comparison
  is not possible: GSC keeps 16 months.
- `pages` (3-month comparison, 12-month series for the top 20 pages), `queries`
  (3-month, all and non-branded), `query_page` (3 months).
- `psi` (first vs latest run in the 3 months), `history` (runs, errors, new and resolved
  alerts per month), `runs` (30 days vs the schedule).
- `candidates` for briefs, defined under `monthly_candidates` in rules.yaml:
  `content_gap`, `rising_query`, `declining_page`, `critical_page_flat`.

`qa` reads `out/monthly.json` too, so briefs that cite a monthly candidate are checked
like weekly recommendations. The report is also stored as
`reports/monthly/<YYYY-MM>.json` on the state branch.

### Publish watch fingerprints (stage 8)

```
python -m seo_agent watch            # incremental: only what changed
python -m seo_agent watch --full     # re-fetch every page (API-triggered runs)
```

- Every page in a snapshot carries a **fingerprint**: hashes of status, redirects, title,
  meta description, canonical, robots meta and X-Robots-Tag, H1/H2, internal links,
  booking link, form, tracking IDs and word count. Per-field hashes say which changed.
- The **build signature** hashes the homepage's own Framer bundle URLs
  (`watch.build_asset_pattern` in site.yaml). Their file names carry a content hash, so
  the signature changes on every publish. Third-party scripts are ignored.
- Each watch run fetches robots.txt, the sitemap and the 15 critical pages (homepage
  included) with `If-None-Match`, so unchanged pages cost a bodiless 304.
- If the build signature, the sitemap's URL set or robots.txt changed, or with `--full`,
  it re-fetches every other page too (still conditionally) and follows new links.
  Otherwise the other pages are carried over unchanged from the previous snapshot; a
  change to a non-critical page without a publish is picked up by the next daily crawl.
- `out/changes.json` lists the mode (`quiet`, `publish` or `full`), the reasons, how many
  pages were fetched, answered 304 or carried, and each changed page with its changed
  fields, plus new and removed pages.

Live, a quiet watch run takes about 17 requests and 16 seconds instead of a ~60-second
full crawl.

### State branch

`seo_agent.state.StateBranch` checks out the state branch in `.state-worktree/`
(an orphan branch, created on first use) and provides `read_json`/`write_json`,
`append_run` (one line per run in `runs.jsonl`) and `commit_and_push`.
