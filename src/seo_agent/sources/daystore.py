"""Day-partitioned row storage on the state branch.

<prefix>/<YYYY-MM>.json = {"days": {"YYYY-MM-DD": fetched_at}, "rows": [{"date": ..., ...}]}
A day listed in "days" was synced (possibly with zero rows). Re-syncing a day replaces
its rows.
"""
import datetime as dt
from collections import defaultdict


def month_of(day: str) -> str:
    return day[:7]


class DayStore:
    def __init__(self, store, prefix: str):
        self.store, self.prefix = store, prefix.rstrip("/")

    def _path(self, month):
        return f"{self.prefix}/{month}.json"

    def _load(self, month):
        return self.store.read_json(self._path(month)) or {"days": {}, "rows": []}

    def synced_days(self) -> set[str]:
        days = set()
        for name in self.store.listdir(self.prefix):
            if name.endswith(".json") and len(name) == 12:
                days |= set(self._load(name[:7])["days"])
        return days

    def put(self, days: list[str], rows: list[dict], fetched_at: str) -> None:
        """Replace the given days (all of them, even if no rows came back)."""
        by_month_rows, by_month_days = defaultdict(list), defaultdict(list)
        for r in rows:
            by_month_rows[month_of(r["date"])].append(r)
        for d in days:
            by_month_days[month_of(d)].append(d)
        for month, mdays in by_month_days.items():
            data = self._load(month)
            dayset = set(mdays)
            kept = [r for r in data["rows"] if r["date"] not in dayset]
            data["rows"] = sorted(kept + by_month_rows[month],
                                  key=lambda r: tuple(str(v) for v in r.values()))
            for d in mdays:
                data["days"][d] = fetched_at
            data["days"] = dict(sorted(data["days"].items()))
            self.store.write_json(self._path(month), data)

    def rows(self, start: str, end: str) -> list[dict]:
        out = []
        m = dt.date.fromisoformat(start).replace(day=1)
        last = dt.date.fromisoformat(end)
        while m <= last:
            out += [r for r in self._load(m.strftime("%Y-%m"))["rows"] if start <= r["date"] <= end]
            m = (m + dt.timedelta(days=32)).replace(day=1)
        return out


def days_to_sync(synced: set[str], start: dt.date, end: dt.date, refresh_last: int) -> list[str]:
    """Missing days in [start, end] plus the last `refresh_last` days up to end (data may still change)."""
    wanted = set()
    d = start
    while d <= end:
        if d.isoformat() not in synced:
            wanted.add(d.isoformat())
        d += dt.timedelta(days=1)
    for i in range(refresh_last):
        day = end - dt.timedelta(days=i)
        if day >= start:
            wanted.add(day.isoformat())
    return sorted(wanted)


def contiguous_ranges(days: list[str], max_len: int = 31) -> list[tuple[str, str]]:
    """Group sorted ISO days into contiguous ranges of at most max_len days."""
    ranges = []
    for day in days:
        d = dt.date.fromisoformat(day)
        if ranges:
            s, e = ranges[-1]
            if d == e + dt.timedelta(days=1) and (d - s).days < max_len:
                ranges[-1] = (s, d)
                continue
        ranges.append((d, d))
    return [(s.isoformat(), e.isoformat()) for s, e in ranges]
