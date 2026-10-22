"""Each daily/watch rule fires on a synthetic breakage of today's snapshot."""
import pytest

from seo_agent.checks import RulesError, run_checks, validate_rules

HOME = "https://arbcentrix.com/"
CONTACT = "https://arbcentrix.com/contact"
TAX = "https://arbcentrix.com/tax"


def fired(snapshot, cfg, rule, previous=None):
    return {f["url"] for f in run_checks(snapshot, cfg, previous) if f["rule"] == rule}


def test_baseline_has_no_alert_rules_firing(snapshot, cfg):
    for rule in ("status_change", "robots_block", "canonical_changed", "sitemap_drop",
                 "tracking_missing", "cta_broken", "broken_internal_link"):
        assert not fired(snapshot, cfg, rule), rule


def test_status_change(snapshot, cfg):
    snapshot["pages"][TAX]["status"] = 500
    snapshot["pages"][CONTACT]["redirects"] = [[301, HOME]]
    assert fired(snapshot, cfg, "status_change") == {TAX, CONTACT}


def test_robots_block_and_missing(snapshot, cfg):
    snapshot["robots"]["rules"] = {"allow": [], "disallow": ["/tax"]}
    assert fired(snapshot, cfg, "robots_block") == {TAX}
    snapshot["robots"]["status"] = 404
    assert fired(snapshot, cfg, "robots_block") == {"https://arbcentrix.com/robots.txt"}


def test_noindex(snapshot, cfg):
    snapshot["pages"][TAX]["noindex"] = True
    assert TAX in fired(snapshot, cfg, "noindex_added")


def test_canonical_elsewhere(snapshot, cfg):
    snapshot["pages"][TAX]["canonicals"] = [HOME]
    assert fired(snapshot, cfg, "canonical_changed") == {TAX}


def test_sitemap_fails(snapshot, cfg):
    snapshot["sitemap"].update(status=500, error="HTTP 500", urls=[])
    assert fired(snapshot, cfg, "sitemap_drop") == {"https://arbcentrix.com/sitemap.xml"}


def test_title_empty(snapshot, cfg):
    snapshot["pages"][TAX]["title"] = "Tax services | Arbcentrix"
    snapshot["pages"][TAX]["meta_description"] = "Offshore tax support."
    assert TAX not in fired(snapshot, cfg, "title_meta_regression")
    snapshot["pages"][TAX]["title"] = ""
    assert TAX in fired(snapshot, cfg, "title_meta_regression")


def test_tracking(snapshot, cfg):
    snapshot["pages"][TAX]["tracking_ids"] = []
    snapshot["pages"]["https://arbcentrix.com/careers"]["tracking_ids"] = ["GTM-N8DDS834", "GTM-PG3F2KGJ"]
    assert fired(snapshot, cfg, "tracking_missing") == {TAX, "https://arbcentrix.com/careers"}


def test_cta(snapshot, cfg):
    snapshot["pages"][CONTACT]["has_form"] = False
    snapshot["pages"][TAX]["has_booking"] = False
    assert fired(snapshot, cfg, "cta_broken") == {CONTACT, TAX}


def test_content_shrink_needs_previous(snapshot, cfg):
    import copy
    previous = copy.deepcopy(snapshot)
    assert not fired(snapshot, cfg, "content_shrink")
    snapshot["pages"][TAX]["word_count"] = int(previous["pages"][TAX]["word_count"] * 0.5)
    assert fired(snapshot, cfg, "content_shrink", previous) == {TAX}


def test_broken_internal_link(snapshot, cfg):
    snapshot["pages"]["https://arbcentrix.com/terms"]["status"] = 404
    assert fired(snapshot, cfg, "broken_internal_link") == {"https://arbcentrix.com/terms"}


def test_watch_subset(snapshot, cfg):
    rules = {f["rule"] for f in run_checks(snapshot, cfg, only_watch=True)}
    assert "orphan_page" not in rules and "title_meta_regression" in rules


def test_rules_validation():
    with pytest.raises(RulesError):
        validate_rules({"checks": [{"id": "nope", "severity": "high"}]})
    with pytest.raises(RulesError):
        validate_rules({"checks": [{"id": "orphan_page", "severity": "urgent"}]})
