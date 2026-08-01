"""
Unit tests for batch/detect_file_duplicates.py's cluster_already_handled() -- pure
decision logic extracted for testability, no DB or filesystem involved.
"""
from batch.detect_file_duplicates import cluster_already_handled


def test_returns_false_when_no_member_is_flagged():
    cluster = ["a", "b", "c"]
    flags = {"a": False, "b": False, "c": False}

    assert cluster_already_handled(cluster, flags) is False


def test_returns_true_when_a_duplicate_is_flagged():
    cluster = ["keeper", "dup1", "dup2"]
    flags = {"keeper": False, "dup1": True, "dup2": False}

    assert cluster_already_handled(cluster, flags) is True


def test_returns_true_when_the_keeper_itself_is_flagged():
    cluster = ["keeper", "dup1"]
    flags = {"keeper": True, "dup1": False}

    assert cluster_already_handled(cluster, flags) is True


def test_missing_id_in_flags_defaults_to_not_flagged():
    cluster = ["a", "b"]
    flags = {"a": False}  # "b" absent

    assert cluster_already_handled(cluster, flags) is False
