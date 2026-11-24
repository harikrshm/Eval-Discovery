"""Console filters: the time range, source and version a console view is narrowed to. Pure: no I/O.

A bounded range is the half-open interval [since, until) with `until` the moment the request was parsed;
`all` has no bounds. Datetimes are naive UTC. The previous period is the same length immediately before,
so a period and its predecessor never overlap and never leave a gap.

`demo` means the row's source is "imported" (generated data), `live` means it is not.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from backend.core.assist.adoption import IMPORTED_SOURCE
from backend.db.models import utcnow

RANGES: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
SOURCES = ("all", "live", "demo")
DEFAULT_RANGE = "all"
DEFAULT_SOURCE = "all"
INVALID_FILTER = "Invalid filter."


@dataclass(frozen=True)
class Filters:
    since: datetime | None
    until: datetime | None
    source: str
    version: int | None


def parse_filters(range_name: str, source: str, version: int | None, now: datetime | None = None) -> Filters:
    """Build the filters; ValueError for an unknown range or source, or a version below 1."""
    if range_name not in RANGES:
        raise ValueError(f"unknown range: {range_name!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
        raise ValueError(f"unknown version: {version!r}")
    length = RANGES[range_name]
    if length is None:
        return Filters(None, None, source, version)
    now = now or utcnow()
    return Filters(now - length, now, source, version)


def previous_period(filters: Filters) -> Filters | None:
    """The same-length period immediately before; None for an unbounded range."""
    if filters.since is None or filters.until is None:
        return None
    length = filters.until - filters.since
    return Filters(filters.since - length, filters.since, filters.source, filters.version)


def bucket_for(filters: Filters) -> str:
    """Hourly buckets for a window of a day or less, daily otherwise (including `all`)."""
    if filters.since is not None and filters.until is not None and filters.until - filters.since <= timedelta(hours=24):
        return "hour"
    return "day"


def matches(source_value: str, filters: Filters) -> bool:
    if filters.source == "demo":
        return source_value == IMPORTED_SOURCE
    if filters.source == "live":
        return source_value != IMPORTED_SOURCE
    return True


def in_range(at: datetime, filters: Filters) -> bool:
    if filters.since is not None and at < filters.since:
        return False
    return filters.until is None or at < filters.until


def keep(at: datetime, source_value: str, assist_version: int | None, filters: Filters) -> bool:
    """Whether a row (page view or question) passes every filter."""
    if not (in_range(at, filters) and matches(source_value, filters)):
        return False
    return filters.version is None or assist_version == filters.version


def kept(
    timed: Iterable[tuple[datetime, Any]], filters: Filters, version_attr: str = "assist_version"
) -> list[tuple[datetime, Any]]:
    """The (timestamp, row) pairs whose row passes every filter. Rows carry `source` and a version attribute.

    The one filter-then-count step: questions and page views keep their version in `assist_version`, model
    calls and judgements in `prompt_version` (pass `version_attr="prompt_version"`).
    """
    return [(at, row) for at, row in timed if keep(at, row.source, getattr(row, version_attr), filters)]


HOUR, DAY = timedelta(hours=1), timedelta(days=1)


def day_start(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class Layout:
    """Equal-width buckets: `size` buckets of `step` starting at `start`."""

    start: datetime
    step: timedelta
    size: int

    def labels(self) -> list[str]:
        return [(self.start + i * self.step).isoformat() for i in range(self.size)]

    def index(self, at: datetime) -> int | None:
        i = (at - self.start) // self.step
        return i if 0 <= i < self.size else None

    def group(self, timed: Iterable[tuple[datetime, Any]]) -> list[list]:
        """The rows of `timed` per bucket, in bucket order; rows outside every bucket are dropped."""
        groups: list[list] = [[] for _ in range(self.size)]
        for at, row in timed:
            if (i := self.index(at)) is not None:
                groups[i].append(row)
        return groups


def bucket_layout(filters: Filters, stamps: list[datetime], now: datetime | None = None) -> Layout | None:
    """The buckets a series is drawn in.

    A bounded range is split into whole buckets counted back from the request time (24 hourly buckets for
    24h, 7 daily for 7d, 30 for 30d). `all` runs in calendar days from the oldest of `stamps` to now; with no
    stamps at all it has no buckets (None).
    """
    step = HOUR if bucket_for(filters) == "hour" else DAY
    if filters.since is not None and filters.until is not None:
        return Layout(filters.since, step, int((filters.until - filters.since) / step))
    if not stamps:
        return None
    start = day_start(min(stamps))
    return Layout(start, step, (day_start(max(now or utcnow(), *stamps)) - start).days + 1)
