"""
Pytest configuration for batch tests.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost:5432/test_db')
os.environ.setdefault('BASE_PATH', '/tmp/test_images')
os.environ.setdefault('APP_ENV', 'general')

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
