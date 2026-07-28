import os
from pathlib import Path

import yaml


class BatchRegistry:
    def __init__(self, base_dir: Path = Path("environments")):
        self._base_dir = base_dir

    def get(self, script_name: str) -> dict | None:
        """Fresh read on every call -- editing the registry file takes effect
        immediately, no backend/scheduler restart needed."""
        return self._load().get(script_name)

    def all_names(self) -> list[str]:
        return list(self._load().keys())

    def name_for_kind(self, kind: str) -> str | None:
        """Reverse lookup -- BatchRun.kind -> public script name."""
        for name, entry in self._load().items():
            if entry["kind"] == kind:
                return name
        return None

    def _load(self) -> dict:
        common = self._read_yaml(self._base_dir / "batch_registry.yaml")
        env = os.environ.get("APP_ENV")
        override = self._read_yaml(self._base_dir / f"batch_registry.{env}.yaml") if env else {}
        return {**common, **override}

    @staticmethod
    def _read_yaml(path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path) as f:
            return yaml.safe_load(f) or {}
