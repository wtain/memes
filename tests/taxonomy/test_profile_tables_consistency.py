import re

from sqlalchemy import CheckConstraint

from Storage.models import MemeProfileValue
from taxonomy.loader import load


def test_axis_check_constraint_matches_the_taxonomy_controlled_axes():
    constraint = next(
        c for c in MemeProfileValue.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == "ck_meme_profile_values_axis"
    )
    assert set(re.findall(r"'(\w+)'", str(constraint.sqltext))) == set(load().controlled_axes())
