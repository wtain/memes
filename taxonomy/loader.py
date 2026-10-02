import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

from taxonomy.model import FREE_TEXT, VOCABULARY, AxisSpec, Taxonomy, VocabValue

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "batch" / "data" / "taxonomy"


class TaxonomyError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (PyYAML silently keeps the last one)."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise TaxonomyError(f"duplicate key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _normalised_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise TaxonomyError(f"missing file: {path.name}")
    return path.read_bytes().replace(b"\r\n", b"\n")


def _read_yaml(path: Path) -> dict:
    data = yaml.load(_normalised_bytes(path).decode("utf-8"), Loader=_UniqueKeyLoader) or {}
    if not isinstance(data, dict):
        raise TaxonomyError(f"{path.name}: top level must be a mapping")
    return data


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parse_axes(raw: dict) -> dict[str, AxisSpec]:
    if not isinstance(raw, dict) or not raw:
        raise TaxonomyError("taxonomy.yaml: 'axes' must be a non-empty mapping")
    axes = {}
    for name, spec in raw.items():
        axis_type = spec.get("type") if isinstance(spec, dict) else None
        if axis_type == FREE_TEXT:
            if not _positive_int(spec.get("max_chars")):
                raise TaxonomyError(f"axis {name!r}: free_text needs a positive integer max_chars")
            axes[name] = AxisSpec(name, FREE_TEXT, max_chars=spec["max_chars"])
        elif axis_type == VOCABULARY:
            if not _positive_int(spec.get("max_values")):
                raise TaxonomyError(f"axis {name!r}: vocabulary needs a positive integer max_values")
            axes[name] = AxisSpec(name, VOCABULARY, max_values=spec["max_values"])
        else:
            raise TaxonomyError(f"axis {name!r}: unknown type {axis_type!r}")
    return axes


def _parse_values(axis: str, raw: dict) -> dict[str, VocabValue]:
    entries = raw.get("values")
    if not isinstance(entries, dict) or not entries:
        raise TaxonomyError(f"{axis}.yaml: 'values' must be a non-empty mapping")
    values = {}
    for key, entry in entries.items():
        if not isinstance(key, str):
            raise TaxonomyError(f"{axis}.yaml: key {key!r} must be a string")
        if not isinstance(entry, dict) or not isinstance(entry.get("definition"), str):
            raise TaxonomyError(f"{axis}.{key}: needs a string 'definition'")
        labels = entry.get("labels")
        if not isinstance(labels, dict) or not all(isinstance(v, str) for v in labels.values()):
            raise TaxonomyError(f"{axis}.{key}: 'labels' must map language to string")
        values[key] = VocabValue(key, entry["definition"], dict(labels))
    return values


def _content_hash(directory: Path, vocabulary_axes: list[str]) -> str:
    digest = hashlib.sha256()
    for name in ["taxonomy.yaml", *sorted(f"{axis}.yaml" for axis in vocabulary_axes)]:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(_normalised_bytes(directory / name) + b"\0")
    return digest.hexdigest()


def _read_overrides(directory: Path) -> tuple[dict, str]:
    """Optional similarity_overrides.yaml: (mapping, sha256 of LF-normalised bytes); ({}, "") when absent.

    Deliberately outside content_hash and the lock: tuning soft-match pairs must not change the version.
    """
    path = directory / "similarity_overrides.yaml"
    if not path.is_file():
        return {}, ""
    return _read_yaml(path), hashlib.sha256(_normalised_bytes(path)).hexdigest()


def _parse(directory: Path) -> Taxonomy:
    root = _read_yaml(directory / "taxonomy.yaml")
    if "similarity_overrides" in root:
        raise TaxonomyError("taxonomy.yaml: 'similarity_overrides' moved to similarity_overrides.yaml")
    version = root.get("version")
    if not _positive_int(version):
        raise TaxonomyError("taxonomy.yaml: 'version' must be a positive integer")
    axes = _parse_axes(root.get("axes"))
    vocabulary_axes = [name for name, spec in axes.items() if spec.type == VOCABULARY]
    values = {axis: _parse_values(axis, _read_yaml(directory / f"{axis}.yaml")) for axis in vocabulary_axes}
    overrides, overrides_hash = _read_overrides(directory)
    return Taxonomy(
        version=version,
        axes=axes,
        values=values,
        content_hash=_content_hash(directory, vocabulary_axes),
        similarity_overrides=overrides,
        overrides_hash=overrides_hash,
    )


@lru_cache(maxsize=1)
def _load_default() -> Taxonomy:
    return _parse(DEFAULT_DIR)


def load(directory: Path | None = None) -> Taxonomy:
    """Load the shipped taxonomy (cached) or one from `directory` (never cached)."""
    if directory is None:
        return _load_default()
    return _parse(Path(directory))
