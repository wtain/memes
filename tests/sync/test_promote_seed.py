"""P2-T10 unit tests: the index state a first seed reports (no database)."""
from sync.promote import cli, preconditions as pre


class FakeProd:
    def __init__(self, names):
        self.names = names

    def query(self, sql, args=None):
        assert "pg_indexes" in sql
        return [(n,) for n in self.names]


EXPECTED = {"embeddings": ["ix_e_hnsw"], "ocr_lemmas": ["ix_l_gin", "ix_l_trgm"]}


def test_every_index_present_is_one_informational_entry():
    assert pre.index_state(FakeProd(["ix_e_hnsw", "ix_l_gin", "ix_l_trgm", "other"]), EXPECTED) == [
        {"id": "rebuild_indexes", "ok": True, "detail": "all_present"}]


def test_a_table_with_a_missing_index_gets_its_own_entry_with_the_count():
    res = pre.index_state(FakeProd(["ix_l_gin"]), EXPECTED)
    assert res == [{"id": "rebuild_indexes:embeddings", "ok": True, "detail": "missing=1"},
                   {"id": "rebuild_indexes:ocr_lemmas", "ok": True, "detail": "missing=1"}]
    assert pre.missing_index_tables(res) == ["embeddings", "ocr_lemmas"]
    assert pre.missing_index_tables([{"id": "rebuild_indexes", "ok": True, "detail": "all_present"}]) == []


def test_the_model_indexes_cover_class_d_tables_only_and_both_methods():
    ix = pre.rebuildable_indexes()
    from sync import registry
    from sync.model import Klass

    assert set(ix) <= set(registry.names(Klass.D)) and ix
    assert all(n.startswith("ix_") or n for names in ix.values() for n in names)


def test_the_seed_steps_are_printed_only_for_a_first_seed():
    import io

    from sync.config import SyncSettings

    s = SyncSettings("general", {"prod": {"database": "memes_general"}}, root=None)
    out = io.StringIO()
    a = cli.build_parser().parse_args(["--env", "general"])
    cli._print_seed_steps(a, s, [], out)
    assert out.getvalue() == ""
    a = cli.build_parser().parse_args(["--env", "general", "--first-seed"])
    cli._print_seed_steps(a, s, [{"id": "rebuild_indexes:embeddings", "ok": True, "detail": "missing=1"}], out)
    text = out.getvalue()
    assert "rebuild the HNSW and GIN indexes of embeddings" in text and "ALTER DATABASE memes_general SET memes.state = 'serving'" in text
