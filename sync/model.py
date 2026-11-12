"""Types of the dev/prod sync registry (epic environment-data-sync, project P1).

Standard library only: the promote and pull tools and the prod migration image import this package without SQLAlchemy.
"""
from dataclasses import dataclass
from enum import Enum


class Klass(str, Enum):
    """Origin class of a table (docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-s1-data-classification.md)."""

    D = "D"  # dev-origin, promoted dev -> prod
    P = "P"  # prod-origin reader activity, pulled prod -> dev
    X = "X"  # dev-only scratch, never synced


class Mode(str, Enum):
    UPSERT_BY_KEY = "upsert_by_key"  # small sets and per-image core rows: insert or update by primary key
    REPLACE_PER_IMAGE = "replace_per_image"  # delete and insert all rows of a changed image (digest-driven)
    MIRROR_REPLACE = "mirror_replace"  # class P on pull: the dev copy is replaced by prod's
    SKIP = "skip"  # class X


class ScopeKind(str, Enum):
    IMAGES = "images"  # the promotable set itself: status active and not flagged
    IMAGE_FK = "image_fk"  # rows whose `column` (an FK to images) is in the promotable set
    VIA = "via"  # rows reachable through `parent` (itself scoped) over `column`
    GLOBAL_SET = "global_set"  # no image relation: moved as a whole set
    PROD_ALL = "prod_all"  # class P: the whole prod table
    NEVER = "never"


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    column: str | None = None
    parent: str | None = None


IMAGES = Scope(ScopeKind.IMAGES)
GLOBAL_SET = Scope(ScopeKind.GLOBAL_SET)
PROD_ALL = Scope(ScopeKind.PROD_ALL)
NEVER = Scope(ScopeKind.NEVER)


def image_fk(column: str = "image_id") -> Scope:
    return Scope(ScopeKind.IMAGE_FK, column=column)


def via(parent: str, column: str) -> Scope:
    return Scope(ScopeKind.VIA, column=column, parent=parent)


PROD_WRITERS = ("none", "backend_app", "sync_writer")

# The promotable set, in one place for P2 and P4: active images that are not flagged (a missing image_extras row, or a NULL
# flagged, means unflagged).
PROMOTABLE_IMAGES_SQL = (
    "images.status = 'active' AND NOT EXISTS "
    "(SELECT 1 FROM image_extras e WHERE e.image_id = images.id AND e.flagged)"
)


@dataclass(frozen=True)
class TableSync:
    name: str
    klass: Klass
    mode: Mode
    scope: Scope
    key: tuple[str, ...]  # the primary key; empty for class X
    level: int | None  # load order, parents first; None for class X
    digest_group: str | None = None  # per-image digest bucket
    prod_writer: str = "none"  # who may write the table on prod (S5 roles)
    nullify: tuple[str, ...] = ()  # FK columns blanked when their target row is not shipped
    setval: bool = False  # integer primary key: the receiving sequence is moved past max(id) after a load
    prune: bool = False  # a global set: rows deleted on dev must be deleted on prod too (upsert alone never deletes)
    pull_before_promote: bool = False  # class P with a CASCADE from images: pull before a promote may retire an image
    notes: str = ""
    # (column, reason) pairs left out of the per-image digest (P2): a change of such a column alone is not a change of the image.
    # Never a column another table references by foreign key; reasons are mandatory.
    digest_exclude: tuple[tuple[str, str], ...] = ()
