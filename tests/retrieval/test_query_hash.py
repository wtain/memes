import hashlib

from retrieval.query_hash import normalised_query, query_hash


def test_normalisation_matches_the_parser_rules():
    assert normalised_query("  Hello \n  world\t", 600) == "Hello world"
    assert normalised_query("a" * 700, 600) == "a" * 600
    assert normalised_query("é", 600) == "é"          # NFC


def test_the_hash_is_sha256_of_the_normalised_text():
    text = normalised_query("My  manager   scheduled a meeting", 600)
    assert query_hash(text) == hashlib.sha256("My manager scheduled a meeting".encode("utf-8")).hexdigest()
    assert len(query_hash(text)) == 64


def test_variants_of_one_message_share_a_hash_and_different_messages_do_not():
    one = query_hash(normalised_query("Hello   world", 600))
    two = query_hash(normalised_query("Hello\nworld ", 600))
    other = query_hash(normalised_query("Hello there", 600))
    assert one == two and one != other
