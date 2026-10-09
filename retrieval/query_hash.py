"""The identity of a message for ratings and exports (P5b): normalise like the parser, then sha256. Pure."""
import hashlib

from parse.parser import normalise_text


def normalised_query(text: str, max_chars: int) -> str:
    return normalise_text(text, max_chars)


def query_hash(normalised: str) -> str:
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
