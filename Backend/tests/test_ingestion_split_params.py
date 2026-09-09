"""Pure unit tests for IngestionService._split_params -- no DB, no settings.yaml dependency.

The config block is built inline and injected by monkeypatching
`Backend.app.services.ingestion_service.settings.get`, so these assertions don't move if the
tracked YAML overlay changes.
"""
import pytest

from Backend.app.services import ingestion_service
from Backend.app.services.ingestion_service import _split_params, _tier_band

FULL_BLOCK = {
    "enabled": True,
    "max_group_size": 12,
    "tier_a": {"decrement": 0.01, "floor": 0.01},
    "tier_b": {"decrement": 0.05, "floor": 0.05},
}


@pytest.fixture
def fake_cfg(monkeypatch):
    """Return a setter: call it with the block _split_params should see (or None)."""
    def _set(block):
        monkeypatch.setattr(
            ingestion_service.settings, "get",
            lambda key, default=None: block if key == "CLUSTERING.INGESTION_REVIEW_SPLITTING" else default,
        )
    return _set


@pytest.mark.parametrize("block", [None, {}, {"enabled": False}])
def test_returns_none_when_disabled_or_absent(fake_cfg, block):
    fake_cfg(block)
    assert _split_params("tier_a") is None


def test_returns_none_when_tier_key_absent(fake_cfg):
    # M3: block enabled but this tier's ladder omitted -> fail open, not KeyError/500.
    fake_cfg({"enabled": True, "max_group_size": 12, "tier_b": {"decrement": 0.05, "floor": 0.05}})
    assert _split_params("tier_a") is None


@pytest.mark.parametrize("tier", ["tier_a", "tier_b"])
def test_full_block_derives_start_from_tier_band(fake_cfg, tier):
    fake_cfg(FULL_BLOCK)
    params = _split_params(tier)
    assert params is not None
    # M4: `start` is the tier's band top, derived -- never read from the config block.
    assert params["start"] == _tier_band(tier)[1]
    assert params["decrement"] == FULL_BLOCK[tier]["decrement"]
    assert params["floor"] == FULL_BLOCK[tier]["floor"]
    assert params["max_size"] == FULL_BLOCK["max_group_size"]
