"""Pieces shared by the 'refresh stale' modes of the group 3 jobs (screenshot recut R3c)."""


def refresh_stats(*, selected: int, refreshed: int, skipped_upstream_stale: int = 0, failed: int = 0) -> dict[str, int]:
    return {"images_selected": selected, "refreshed": refreshed, "skipped_upstream_stale": skipped_upstream_stale,
            "failed": failed, "images_remaining": selected - refreshed}


def reject_conflicts(parser, refresh_stale: bool, **flags: bool) -> None:
    """--refresh-stale only selects stale rows of cuts; a flag that clears or widens the selection must not be combined
    with it."""
    if not refresh_stale:
        return
    for name, enabled in flags.items():
        if enabled:
            parser.error(f"--refresh-stale cannot be combined with --{name.replace('_', '-')}")
