"""The bounce rule for a Patent Assist page view. Pure: shared by the live tracker and the importer."""

BOUNCE_MAX_DWELL_MS = 10_000
BOUNCE_ENGAGED_SECTION_MS = 3_000


def is_bounce(dwell_ms: int, sections: dict) -> bool:
    """A bounce is a short visit (under 10 s) in which no section was visible for 3 s or more."""
    return dwell_ms < BOUNCE_MAX_DWELL_MS and not any(
        ms >= BOUNCE_ENGAGED_SECTION_MS for ms in sections.values()
    )
