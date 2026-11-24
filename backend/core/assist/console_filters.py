"""Console filters: the time range, source and version a console view is narrowed to. Pure: no I/O.

A bounded range is the half-open interval [since, until) with `until` the moment the request was parsed;
`all` has no bounds. Datetimes are naive UTC. The previous period is the same length immediately before,
so a period and its predecessor never overlap and never leave a gap.

`demo` means the row's source is "imported" (generated data), `live` means it is not.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

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
