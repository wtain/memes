
class UnionFind:
    """
    Union-Find (Disjoint Set Union) for building similarity clusters.

    Elements are added lazily on first reference. Supports any hashable
    type as item identifiers (strings, filenames, etc.).

    Uses union by rank + path compression — O(α(n)) per operation.
    """

    def __init__(self):
        self._parent: dict = {}   # item → parent item
        self._rank: dict = {}     # item → tree height estimate
        self._members: dict = {}  # root → set of members in cluster

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _add(self, item) -> None:
        """Register a new item as its own singleton cluster."""
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = 0
            self._members[item] = {item}

    def _find_root(self, item):
        """Return root with path compression (flattens the tree in place)."""
        if self._parent[item] != item:
            self._parent[item] = self._find_root(self._parent[item])
        return self._parent[item]

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def connect(self, i, j) -> None:
        """Merge the clusters containing i and j."""
        self._add(i)
        self._add(j)

        root_i = self._find_root(i)
        root_j = self._find_root(j)

        if root_i == root_j:
            return  # already in the same cluster

        # Union by rank: attach smaller tree under larger tree
        if self._rank[root_i] < self._rank[root_j]:
            root_i, root_j = root_j, root_i  # root_i is always the winner

        self._parent[root_j] = root_i
        self._members[root_i] |= self._members.pop(root_j)

        if self._rank[root_i] == self._rank[root_j]:
            self._rank[root_i] += 1

    def get(self, i):
        """Return the root (representative) of the cluster containing i."""
        self._add(i)
        return self._find_root(i)

    def list_clusters(self) -> list:
        """Return a list of all current cluster roots."""
        return list(self._members.keys())

    def get_cluster(self, i) -> list:
        """Return all items in the same cluster as i."""
        root = self.get(i)
        return list(self._members[root])
