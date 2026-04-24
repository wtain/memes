import sys
from pathlib import Path

# Add parent to path
project_root = Path(__file__).parent.parent.parent.parent  # Go up 4 levels
sys.path.insert(0, str(project_root))

try:
    from batch.graph.uf import UnionFind
except ImportError as e:
    print(e)
    raise


__all__ = [
    'UnionFind',
]
