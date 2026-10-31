"""rollout-prep preflight: the checks that need no database (tables tie, filesystem, base path, report)."""
from Storage.models import Base
from tools import rollout_preflight as rp


def test_every_expected_name_exists_in_the_models_and_the_chain():
    ids = {r.revision for r in rp.chain()}
    for table, columns, revision in rp.EXPECTED:
        assert table in Base.metadata.tables, table
        for column in columns:
            assert column in Base.metadata.tables[table].columns, (table, column)
        assert revision in ids, revision
