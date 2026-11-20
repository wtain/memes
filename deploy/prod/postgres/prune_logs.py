#!/usr/bin/env python3
"""Prune the Postgres log directory of one environment to a fixed bound (observability spec 6.3).

    python3 prune_logs.py /srv/memes/<env>/logs/postgres [--dry-run]       (or: provision.sh prune-logs <env>)

postgresql.conf starts a NEW file at every rotation (log_filename carries the second, log_rotation_size = 20MB, log_rotation_age = 1d),
so nothing in Postgres bounds the number of files: this script does. It keeps the newest MAX_FILES files whose total stays within
MAX_BYTES and deletes the rest, oldest first. The newest file (the one Postgres is writing) is never deleted. Only regular files named
postgresql-YYYYMMDD-HHMMSS.log are considered; anything else, and any symlink, is left alone.

The byte bound is MAX_BYTES; sync.verify's 80% warning for this directory uses 0.8 * MAX_BYTES. Between two runs of this script the
directory can exceed it by what Postgres writes in that time (each file stops at 20MB, so growth is at most 20MB per rotation).
"""
import os
import re
import sys

MAX_FILES = 10
MAX_BYTES = 200 * 1024 * 1024  # 200 MiB per environment
NAME = re.compile(r"postgresql-\d{8}-\d{6}\.log")


def select_deletions(files, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """files: [(name, size)]. Names to delete: newest first (the timestamp in the name sorts), keep while within both bounds, newest always kept."""
    kept_files, kept_bytes, doomed = 0, 0, []
    for name, size in sorted(files, reverse=True):
        if kept_files == 0 or (kept_files < max_files and kept_bytes + size <= max_bytes):
            kept_files += 1
            kept_bytes += size
        else:
            doomed.append(name)
    return doomed


def prune(directory, dry_run=False, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    files = []
    for entry in os.scandir(directory):
        if NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
            files.append((entry.name, entry.stat(follow_symlinks=False).st_size))
    doomed = select_deletions(files, max_files, max_bytes)
    for name in doomed:
        if not dry_run:
            os.unlink(os.path.join(directory, name))
    return doomed


def main(argv):
    args = [a for a in argv[1:] if a != "--dry-run"]
    if len(args) != 1 or not os.path.isdir(args[0]):
        print("usage: prune_logs.py <log directory> [--dry-run]", file=sys.stderr)
        return 2
    doomed = prune(args[0], dry_run="--dry-run" in argv)
    print(f"prune_logs: {len(doomed)} file(s) {'would be ' if '--dry-run' in argv else ''}removed from {args[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
