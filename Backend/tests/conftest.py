"""
Pytest configuration and shared fixtures for backend tests.
"""
import os
import pytest
import sys
from pathlib import Path

# Set up environment variables BEFORE any imports
# This prevents RuntimeError from Storage.config
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost:5432/test_db')

# Add the project root to the Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope="session")
def anyio_backend():
    """Configure async backend for pytest-anyio."""
    return "asyncio"
