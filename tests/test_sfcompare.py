import csv
import json

from seo_agent.cli import main
from seo_agent.sfcompare import compare, ours, read_sf


def _export(snapshot, path, tweak=lambda rows: None):
    rows = [{"Address": u, "Content Type": "text/html; charset=utf-8", "Status Code": v["status"],
             "Title 1": v["title"], "Canonical Link Element 1": v["canonical"], "Unique Inlinks": v["inlinks"]}
            for u, v in ours(snapshot).items()]
    tweak(rows)
    rows.append({"Address": "https://arbcentrix.com/logo.png", "Content Type": "image/png", "Status Code": 200,
                 "Title 1": "", "Canonical Link Element 1": "", "Unique Inlinks": 3})
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return path


def test_identical_export_passes(snapshot, tmp_path):
    r = compare(read_sf(_export(snapshot, tmp_path / "sf.csv")), ours(snapshot))
    assert r["pass"] and r["urls"] == len(snapshot["pages"]) and not r["mismatches"]


def test_threshold_is_per_field(snapshot, tmp_path):
    def tweak(rows):
        for r in rows[:4]:  # 4 titles wrong: below 95% of the pages
            r["Title 1"] = "other"
    r = compare(read_sf(_export(snapshot, tmp_path / "sf.csv", tweak)), ours(snapshot))
    assert not r["pass"] and r["fields"]["title"]["rate"] < 0.95 and r["fields"]["status"]["rate"] == 1


def test_url_missing_from_sf_is_a_mismatch(snapshot, tmp_path):
    r = compare(read_sf(_export(snapshot, tmp_path / "sf.csv", lambda rows: rows.pop())), ours(snapshot))
    assert r["fields"]["status"]["agree"] == r["urls"] - 1


def test_cli(snapshot, tmp_path):
    (tmp_path / "snapshot.json").write_text(json.dumps(snapshot))
    sf = _export(snapshot, tmp_path / "sf.csv")
    assert main(["compare-sf", str(sf), "--snapshot", str(tmp_path / "snapshot.json")]) == 0
