import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `from rules.engine import ...` works
# regardless of the directory pytest is invoked from.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set a dummy DATABASE_URL before any test module imports Storage.db (directly
# or transitively) — Storage.config raises RuntimeError at import time if
# DATABASE_URL is unset, and create_async_engine() doesn't connect eagerly, so
# a placeholder value is sufficient for unit tests that never open a real
# session. Same convention as Backend/tests/conftest.py and
# tests/integration/conftest.py.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("BASE_PATH", "/tmp/test_images")