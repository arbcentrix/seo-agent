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

### State branch

`seo_agent.state.StateBranch` checks out the state branch in `.state-worktree/`
(an orphan branch, created on first use) and provides `read_json`/`write_json`,
`append_run` (one line per run in `runs.jsonl`) and `commit_and_push`.
