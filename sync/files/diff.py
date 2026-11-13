"""P3-T1: the dev manifest compared with the gate's `list` records (pure, no I/O).

Only regular files with one link (`type f`, `nlink 1`) are diffed; every other entry on prod is an anomaly, reported and left alone.
Categories feed `sync.report` (interfaces.md section k). Design: P3 promote flow steps 2 to 5.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sync import report
from sync.files.manifest import Manifest

HALF = 0.5


@dataclass(frozen=True)
class RemoteEntry:
    type: str
    size: int
    sha256: str  # recorded by the gate, empty when it never placed or hashed the file
    nlink: int
    uid: int
    mode: str
    name: str
    invalid: bool = False  # name with tab/newline/NUL or non-UTF-8 bytes, or a malformed record


@dataclass(frozen=True)
class Anomaly:
    name: str  # raw name (may be hostile; use report_name() before it reaches a report)
    kind: str  # type letter of the gate, `invalid` or `malformed`
    reason: str  # not_regular, hardlinked, invalid_name, malformed_record
    in_manifest: bool = False  # a manifest name sits on a non-regular entry (the commit would replace or fail)


@dataclass
class Diff:
    added: list = field(default_factory=list)  # manifest entries missing on prod
    size_changed: list = field(default_factory=list)  # (entry, remote)
    hash_changed: list = field(default_factory=list)
    unverified: list = field(default_factory=list)  # on prod, size equal, no recorded hash: not trusted, re-sent unless --deep adopts it
    unchanged: list = field(default_factory=list)
    left_behind: list = field(default_factory=list)  # names of regular files on prod that are not in the manifest
    anomalies: list = field(default_factory=list)
    prod_regular_count: int = 0

    @property
    def to_send(self) -> list:
        """Manifest entries that must travel, sorted by name."""
        out = list(self.added) + [e for e, _ in self.size_changed] + [e for e, _ in self.hash_changed] + list(self.unverified)
        return sorted(out, key=lambda e: e.filename.encode("utf-8", "surrogatepass"))

    @property
    def replaced_count(self) -> int:
        return len(self.size_changed) + len(self.hash_changed)

    @property
    def blocking_anomalies(self) -> list:
        return [a for a in self.anomalies if a.in_manifest and a.kind in ("d", "invalid", "malformed")]


# ---------------------------------------------------------------------------------------------------------------------------------
# gate `list` output: type TAB size TAB sha256 TAB nlink TAB uid TAB mode TAB name NUL

def parse_list(data: bytes) -> list:
    """Parse the records of the gate's `list` verb. Never raises on hostile content: bad records become `invalid` RemoteEntry values."""
    out = []
    for raw in data.split(b"\0"):
        if not raw:
            continue
        text = raw.decode("utf-8", "surrogateescape")
        parts = text.split("\t", 6)
        if len(parts) != 7:
            out.append(RemoteEntry("?", 0, "", 0, 0, "", text, invalid=True))
            continue
        typ, size, sha, nlink, uid, mode, name = parts
        try:
            size_i, nlink_i, uid_i = int(size), int(nlink), int(uid)
        except ValueError:
            out.append(RemoteEntry("?", 0, "", 0, 0, "", name, invalid=True))
            continue
        invalid = typ == "invalid" or _bad_text(name)
        out.append(RemoteEntry(typ, size_i, sha.strip().lower(), nlink_i, uid_i, mode, name, invalid))
    return out


def _bad_text(name: str) -> bool:
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return True  # surrogateescape residue = non-UTF-8 bytes
    return any(ord(c) < 32 or ord(c) == 127 for c in name) or "/" in name or name == ""


# ---------------------------------------------------------------------------------------------------------------------------------
# diff

def diff_manifest(manifest: Manifest, remote: list) -> Diff:
    d = Diff()
    usable: dict = {}
    for r in remote:
        if r.invalid:
            d.anomalies.append(Anomaly(r.name, "invalid" if r.type != "?" else "malformed",
                                       "invalid_name" if r.type != "?" else "malformed_record"))
        elif r.type != "f":
            d.anomalies.append(Anomaly(r.name, r.type, "not_regular", r.name in manifest.entries))
        elif r.nlink != 1:
            d.anomalies.append(Anomaly(r.name, r.type, "hardlinked", r.name in manifest.entries))
        elif r.name in usable:
            d.anomalies.append(Anomaly(r.name, r.type, "duplicate_record"))
        else:
            usable[r.name] = r
    d.prod_regular_count = len(usable)
    # A manifest name that sits on an anomalous entry is still planned as added (the gate decides); the anomaly is flagged.

    for name in sorted(manifest.entries, key=lambda n: n.encode("utf-8", "surrogatepass")):
        e = manifest.entries[name]
        r = usable.get(name)
        if r is None:
            d.added.append(e)
        elif r.size != e.size:
            d.size_changed.append((e, r))
        elif not r.sha256:
            d.unverified.append(e)
        elif r.sha256 != e.sha256:
            d.hash_changed.append((e, r))
        else:
            d.unchanged.append(e)
    d.left_behind = sorted((n for n in usable if n not in manifest.entries), key=lambda n: n.encode("utf-8", "surrogatepass"))
    return d


# ---------------------------------------------------------------------------------------------------------------------------------
# report feeding

def report_name(name: str) -> str:
    """A file name that is safe to put in a report; hostile names are replaced by a stable placeholder."""
    try:
        name.encode("utf-8")
        return report.check_file_name(name)
    except (report.ReportError, UnicodeEncodeError):
        digest = hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()[:12]
        return f"invalid-name-{digest}"


def guards(manifest: Manifest, prod_regular_count: int) -> list:
    """Client-side guards. The report shows them tripped or not; an apply refuses (exit 3) when any is tripped. Not liftable."""
    n = len(manifest.entries)
    return [
        report.Guard("empty_manifest", n == 0, n, 1),
        report.Guard("manifest_below_half_of_prod", prod_regular_count > 0 and n < prod_regular_count * HALF, n, int(prod_regular_count * HALF)),
        report.Guard("left_out_tripwire", manifest.tripwire_tripped, len(manifest.left_out), int(0.005 * manifest.promotable_count)),
    ]


def to_entry(manifest: Manifest, d: Diff, name: str = "images") -> report.Entry:
    """The `files` entry of the report: added, overwritten (size/hash split), unchanged, skipped with reason, left behind."""
    skipped: dict = {}
    for reason, ids in manifest.ids_by_reason().items():
        skipped[reason] = report.IdSet(ids=ids, bytes=0)
    resend = [e.id for e in d.unverified]
    changed = [e for e, _ in d.size_changed] + [e for e, _ in d.hash_changed]
    over = report.Overwritten(
        ids=[e.id for e in changed] + resend,
        bytes=sum(e.size for e in changed) + sum(e.size for e in d.unverified),
        size_changed=[e.id for e, _ in d.size_changed],
        hash_changed=[e.id for e, _ in d.hash_changed],
    )
    names = [report_name(n) for n in d.left_behind] + [report_name(a.name) for a in d.anomalies if not a.in_manifest]
    return report.Entry(
        name=name, kind="files",
        added=report.IdSet(ids=[e.id for e in d.added], bytes=sum(e.size for e in d.added)),
        overwritten=over,
        unchanged=report.IdSet(ids=[e.id for e in d.unchanged], bytes=sum(e.size for e in d.unchanged)),
        skipped=skipped,
        left_behind=report.LeftBehind(names=sorted(set(names))),
    )
