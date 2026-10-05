import dataclasses
from typing import Sequence

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.retrievers.axis_match import AxisMatchRetriever
from parse.prompt import PARSER_VERSION


class ParsedAxisMatchRetriever:
    """Axis match with the query's PARSED profile instead of the gold expected profile (P3 spec, decision 9): the
    scoring is exactly `axis_match`'s, so the difference to that baseline is purely what parse errors cost."""

    name = "parsed_axis_match"

    def __init__(self, session, parser):
        self._inner = AxisMatchRetriever(session)
        self._parser = parser
        self._parsed: dict[str, ExpectedProfile] = {}
        self.config = {"parseModel": getattr(parser, "model", ""), "parserVersion": PARSER_VERSION}

    async def prepare(self, queries: Sequence[GoldQuery]) -> None:
        for query in queries:
            result = await self._parser.parse(query.text)
            profile = result.profile
            self._parsed[query.id] = ExpectedProfile(
                situation=profile.get("situation") or "",
                emotion=tuple(value["key"] for value in profile["emotion"]),
                stance=tuple(value["key"] for value in profile["stance"]),
                topic=tuple(value["key"] for value in profile["topic"]),
                tone=tuple(value["key"] for value in profile["tone"]),
            )

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        if query.id not in self._parsed:
            await self.prepare([query])
        parsed_query = dataclasses.replace(query, expected=self._parsed[query.id])
        return await self._inner.retrieve(parsed_query, corpus_ids, k)
