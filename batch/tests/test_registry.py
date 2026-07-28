"""
Unit tests for batch/registry.py's BatchRegistry -- a hot-reloadable, YAML-backed
allow-list. Uses tmp_path fixture files rather than the real environments/ directory,
so these tests don't depend on (or risk breaking) the real registry contents.
"""
import pytest

from batch.registry import BatchRegistry


def _write_common(base_dir, content):
    (base_dir / "batch_registry.yaml").write_text(content)


class TestBatchRegistry:
    def test_get_returns_entry_for_known_script(self, tmp_path):
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)

        entry = registry.get("trends_batch")

        assert entry == {"module": "batch.trends_batch", "kind": "trends"}

    def test_get_returns_none_for_unknown_script(self, tmp_path):
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)

        assert registry.get("does_not_exist") is None

    def test_all_names_lists_every_entry(self, tmp_path):
        _write_common(
            tmp_path,
            "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n"
            "move_flagged:\n  module: batch.move_flagged\n  kind: move_flagged\n",
        )
        registry = BatchRegistry(base_dir=tmp_path)

        assert set(registry.all_names()) == {"trends_batch", "move_flagged"}

    def test_name_for_kind_reverse_lookup(self, tmp_path):
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)

        assert registry.name_for_kind("trends") == "trends_batch"
        assert registry.name_for_kind("no_such_kind") is None

    def test_reads_fresh_on_every_call_no_caching(self, tmp_path):
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)
        assert registry.get("move_flagged") is None

        _write_common(
            tmp_path,
            "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n"
            "move_flagged:\n  module: batch.move_flagged\n  kind: move_flagged\n",
        )

        # Same BatchRegistry instance, no restart/reload call -- must see the edit.
        assert registry.get("move_flagged") == {"module": "batch.move_flagged", "kind": "move_flagged"}

    def test_per_environment_override_extends_common(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APP_ENV", "metal")
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        (tmp_path / "batch_registry.metal.yaml").write_text(
            "metal_only_job:\n  module: batch.metal_only\n  kind: metal_only\n"
        )
        registry = BatchRegistry(base_dir=tmp_path)

        assert set(registry.all_names()) == {"trends_batch", "metal_only_job"}

    def test_missing_per_environment_override_file_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APP_ENV", "it")
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)  # no batch_registry.it.yaml exists

        assert registry.all_names() == ["trends_batch"]
