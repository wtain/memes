"""
Presentational splitting of oversized ingestion-review clusters.

IngestionService.list_clusters groups a tier's candidate pairs with a flat union-find, which
merges loose A~B~C~...~Z chains into one 200+ member review card. This splits such a card into
tight subgroups, reusing batch.clusterize.resolve_cluster for the tight-core detection.

Unlike resolve_cluster -- which drops members left isolated at a tighter threshold as implicit
singletons (fine for confirmed-duplicate shaping) -- split_for_review is LOSSLESS: its output
groups are an exact partition of the input. A member dropped from ingestion review is a latent
unreviewable candidate pair that silently blocks ingest_promote.

See docs/superpowers/specs/2026-09-09-ingestion-review-cluster-splitting-design.md.
"""
import heapq

from batch.clusterize import resolve_cluster
from graph.uf import UnionFind


def split_for_review(members, pairs_by_member, *, start, decrement, floor, max_size):
    """Partition an oversized ingestion-review blob into tight subgroups.

    members: ids in one union-find component (any hashable; UUIDs in practice).
    pairs_by_member: id -> list[(neighbor_id, distance)], symmetric, covering every in-band
        pair among `members`. Entries for ids outside `members` are ignored. A real blob
        always has at least one edge, so this is never effectively empty in production.
    start / decrement / floor: the tier's threshold ladder (see the spec's config block).
    max_size: groups at or below this are left whole.

    Returns id-lists that partition `members` exactly -- every input id in exactly one group,
    none dropped, none duplicated. A group may exceed max_size when the tight core hit `floor`
    still oversized or a residual sub-component has no tight structure; the frontend collapses
    oversized cards.
    """
    members = list(members)
    if len(members) <= max_size:
        return [members]

    member_set = set(members)

    # 1. Tight cores via the existing recursive splitter. May omit members (implicit singletons).
    cores = [list(c) for c in resolve_cluster(
        members, pairs_by_member, start, decrement, floor, max_size,
    )]

    # 2. Re-attach every omitted member to the core holding its tightest edge, globally
    #    tightest first, so a chain of loose members drains toward the core it hangs off.
    #    Prim-style frontier: the heap holds, per loose member reachable from an assigned
    #    member, that member's tightest such edge. Pop the global tightest, attach it, then
    #    push its still-loose neighbours (now reachable). O(E log E). The naive
    #    rescan-all-loose-each-round version is O(loose^2 * degree), which never returns on a
    #    Tier B blob where `loose` is tens of thousands (23k-member component on `general`).
    #    Heap key: (distance, str(id), core_index) -- str(id) makes an equidistant loose
    #    member's choice order-independent, core_index breaks a same-member same-distance tie
    #    deterministically; the trailing id keeps the tuple comparable without ever mattering.
    assigned = {m: i for i, core in enumerate(cores) for m in core}
    loose = member_set - assigned.keys()
    frontier = []
    for m in loose:
        for neighbor, distance in pairs_by_member.get(m, ()):
            if neighbor in assigned:
                heapq.heappush(frontier, (distance, str(m), assigned[neighbor], m))
    while frontier and loose:
        distance, _, core_index, m = heapq.heappop(frontier)
        if m not in loose:
            continue  # already attached via a tighter edge
        cores[core_index].append(m)
        assigned[m] = core_index
        loose.discard(m)
        for neighbor, nd in pairs_by_member.get(m, ()):
            if neighbor in loose:
                heapq.heappush(frontier, (nd, str(neighbor), core_index, neighbor))

    # 3. Residual: loose ids that reach no core. They were all in one blob, so they're
    #    connected among themselves at the tier's outer threshold -- union-find their mutual
    #    edges and emit each component. In practice this step now rarely has work to do --
    #    batch/clusterize.py's resolve_cluster() (as of
    #    docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md) never
    #    returns an empty core list for a >= 2-member input, so `loose` is usually empty too.
    #    Kept as a defensive fallback for any future resolve_cluster() change that reintroduces
    #    a case where it returns fewer members than it was given.
    if loose:
        uf = UnionFind()
        for m in loose:
            uf.get(m)  # register even an id with no surviving mutual edge
            for neighbor, _ in pairs_by_member.get(m, ()):
                if neighbor in loose:
                    uf.connect(m, neighbor)
        for root in uf.list_clusters():
            cores.append(list(uf.get_cluster(root)))

    return [c for c in cores if c]
