# S4 Discovery Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only command, `python -m tools.discovery_report {memes,queries}`, that lists the memes which keep coming back through ingestion next to their visibility (impressions, saves, popularity) and the searches that found nothing or little, so the owner can tell "popular" from "hard to find".

**Architecture:** `repository/discovery_report.py` holds two read-only queries (`memes_report`, `queries_report`) that return dataclass rows; `tools/discovery_report.py` is the command: options, a read-only connection (reusing the rollout preflight's role and target checks), an aligned text table and optional CSV. No migration, no schema, no API, no client change.

**Tech Stack:** PostgreSQL (recursive CTEs, window functions), SQLAlchemy async `text()`, argparse, csv.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s4-discovery-report-design.md` (binding; read it first).

## Global Constraints

- Work only in the worktree. Never run a migration, restart a backend or touch a live database (`it`, `general`, `metal`); the owner does that. Subagents never receive the main `DATABASE_URL`; the tool is only ever pointed at `DATABASE_URL_READONLY`, and tests use the test database.
- Never bind ports 8081-8083, 5173-5175 or the 5432/5434/5436 servers for anything but the test database `ocrdb_test` on `ocr-db`, port 5432. Other sessions share it: before an integration run check no other `pytest tests/integration` process is running, or use a private scratch database (`ocrdb_test_s4` with `vector` and `pg_trgm`, dropped afterwards, never `ocrdb`).
- Python: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`. Run each pytest root separately (`Backend/tests`, `tests/integration`, `tests/docs`, `batch/tests`, `tests/rules`). Integration runs need `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line. Async integration tests carry `@pytest.mark.asyncio(loop_scope="session")` and use the rolled-back `db_session` fixture. Sync tests live in files without a module-level asyncio `pytestmark`.
- Repositories never call `session.commit()`; the report never writes. The tool never imports `Storage.db` and never calls `load_env()` (it would swap in the write-capable URL). ORM models only in `Storage/models.py`.
- No SQL string is built from user input: `--sort`, `--kind` and `--sources` are validated against fixed sets and mapped to fixed SQL fragments; everything else is a bound parameter.
- The query text is private: never log it, never put it in an error message, print it only to stdout. The database URL and password are never printed (use `rollout_preflight.scrub`).
- Use the Edit tool for repo text (never PowerShell Get/Set-Content round trips: BOM and double-encoding). Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Inputs and conditions the spec implies and a person would hit; each has a test in the owning task.

1. A tree whose only counted edges sit below an excluded edge (root R <- L through `cluster_review`, L <- M through `hash_cross_corpus`, with `--sources hash_cross_corpus`) is still listed under R: roots are the true roots of the forest, not the winners of counted edges (Task 1).
2. Ratios and empty states: `per_100` is `inf` at 0 impressions with re-drops, 0 with no re-drops in the window, `n/a` when `meme_impressions` does not exist, and the report still runs and sorts sensibly without that table (Task 1).
3. The tail-page rule neither hides a genuine failure nor shows a finished search: a repeated query with equal low counts is still a failure, a low row for another client or more than 30 minutes after a higher one is still a failure, a low row within 30 minutes after a higher one from the same client is not (Task 2).
4. Privacy and safety: a role that can write is refused, no URL or password is ever printed, a bad `--sort`, `--kind` or `--sources` value is a usage error with no SQL run, and nothing is written without `--csv` (Task 3).
5. An empty result prints a header and "0 rows" and exits 0 rather than failing; `--csv` with zero rows still writes the header (Task 3).

---

### Task 1: Memes view in the repository

**Files:**
- Create: `repository/discovery_report.py` (this task: `MemeRow`, `MEME_SORTS`, `memes_report`)
- Test: `tests/integration/test_discovery_report_memes.py`

**Interfaces:**
- Consumes: `OPEN_EDGE_SQL` from `repository/popularity.py` is a string using alias `a` (`a.kind = 'absorb' AND a.reversed_by_id IS NULL`); `Storage.models` (`Image`, `ImageExtras`, `MemeSave`, `MemeImpression`, `PopularityAbsorption`, `PopularitySource`); `PopularityRepository.absorb(loser_id, winner_id, source)` and `reverse_open_edge(loser_id, reason)`; the `make_image` helper from `tests/integration/test_popularity_repository.py`; `MemeImpressionRepository.record_many`.
- Produces:
  - `DEFAULT_SOURCES: tuple[str, ...]` = every `PopularitySource.ALL` member except `adjustment`.
  - `MEME_SORTS = ("per_100", "redrops", "redrops_window", "popularity", "saves", "impressions")`.
  - `@dataclass(frozen=True) class MemeRow` with `image_id: uuid.UUID`, `filename: str | None`, `redrops: int`, `redrops_window: int`, `sources: dict[str, int]`, `popularity: int`, `saves: int`, `impressions: int | None`, `impressions_window: int | None`, `last_back: datetime | None`, `per_100: float | None` (`inf` allowed; `None` = n/a).
  - `async def memes_report(session, *, window_days: int, sources: tuple[str, ...] = DEFAULT_SOURCES, min_redrops: int = 1, max_impressions: int | None = None, sort: str = "per_100", limit: int = 50) -> tuple[list[MemeRow], bool]` returning the rows and whether impressions were available. Raises `ValueError` for an unknown source, an unknown sort, `limit < 1`, or `max_impressions` given while `meme_impressions` does not exist.

- [ ] **Step 1: Write the failing tests** in `test_discovery_report_memes.py`. Local helpers: `edge(session, winner, loser, source, weight=1, created_at=None)` inserting a `PopularityAbsorption(kind="absorb", ...)` row directly (`loser` may be `None` for a re-dropped file; `created_at` only set when given), and `rows(...)` returning `{row.image_id: row}`. Tests:
  - `test_counts_a_re_drop_for_the_winner`: one `hash_cross_corpus` edge with loser `None` onto an active image: one row, `redrops == 1`, `sources == {"hash_cross_corpus": 1}`, `popularity` equals the image's cached `popularity` column.
  - `test_a_chain_is_credited_to_the_final_root`: edge `c`→`b` (`ingest_tier_a`), `b`→`a` (`ingest_tier_b`): only `a` is listed, `redrops == 2`, `sources == {"ingest_tier_a": 1, "ingest_tier_b": 1}`.
  - `test_a_tree_below_an_excluded_edge_is_still_found` (Review Focus 1): `L`→`R` through `cluster_review`, `M`→`L` through `hash_cross_corpus`; with `sources=("hash_cross_corpus",)` the row is `R` with `redrops == 1`.
  - `test_adjustment_is_excluded_by_default_and_other_sources_are_counted`: an `adjustment` edge (weight 5, loser `None`) plus a `file_duplicates` edge: default call counts only the latter; `sources=("adjustment",)` counts 5.
  - `test_weight_zero_recut_edges_add_nothing`: only a weight-0 `recut` edge: no row.
  - `test_reversed_edges_do_not_count`: absorb then `reverse_open_edge(l.id, "unflag")`: no row for the winner.
  - `test_window_split`: three `hash_cross_corpus` edges for one image with `created_at` 5 days, 29 days 23 hours and 40 days ago; `window_days=30`: `redrops == 3`, `redrops_window == 2`, `last_back` equals the newest `created_at`.
  - `test_flagged_non_active_and_deleted_roots_are_not_listed`: three winners with a counted edge each: one flagged (`ImageExtras(flagged=True)`), one `pending`, one whose `Image` row is deleted: no rows.
  - `test_saves_and_impressions_include_the_whole_tree`: loser edge `l`→`w`; `MemeSave` rows on both and `record_many("search", [l.id, w.id])` plus one 40-day-old impression row on `w`: `saves == 2`, `impressions == 3`, `impressions_window == 2`.
  - `test_per_100_is_inf_zero_or_a_ratio`: image A with 2 window re-drops and 0 impressions (`inf`); image B with 0 window re-drops (old edges only) and 5 impressions (`0.0`, needs `min_redrops=0`); image C with 3 window re-drops and 6 impressions (`50.0`).
  - `test_min_redrops_max_impressions_and_limit`: three images with different counts; `min_redrops=2` drops the 1-re-drop image; `max_impressions=0` keeps only never-shown ones; `limit=1` returns one row.
  - `test_every_sort_orders_descending_and_the_default_is_per_100`: a fixture of three images whose six columns rank differently; assert the id order for each name in `MEME_SORTS`, and that the default equals `per_100`.
  - `test_without_the_impressions_table_the_columns_are_n_a_and_the_sort_falls_back` (Review Focus 2): inside the test transaction run `DROP TABLE meme_impressions`; `memes_report` returns `available == False`, rows with `impressions is None`, `impressions_window is None`, `per_100 is None`, ordered by `redrops_window` then `redrops` descending; `max_impressions=1` raises `ValueError`.
  - `test_invalid_arguments_raise_before_any_sql`: unknown source, unknown sort, `limit=0`.
  The test DB is rebuilt with `create_all`, so `meme_impressions` exists in every test except the one that drops it in its rolled-back transaction.

- [ ] **Step 2: Run to verify they fail** (`ModuleNotFoundError: repository.discovery_report`).
  Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_discovery_report_memes.py -v`

- [ ] **Step 3: Implement `memes_report`.** One statement built from fixed fragments. Skeleton (the f-string pieces are fixed text; only bound parameters carry user values):
  ```python
  _TREE = f"""
  WITH RECURSIVE
  roots AS (
      SELECT DISTINCT a.winner_image_id AS root_id FROM popularity_absorptions a
      WHERE {OPEN_EDGE_SQL}
        AND NOT EXISTS (SELECT 1 FROM popularity_absorptions l
                        WHERE l.kind = 'absorb' AND l.reversed_by_id IS NULL AND l.loser_image_id = a.winner_image_id)
  ),
  tree(root_id, id) AS (
      SELECT root_id, root_id FROM roots
      UNION
      SELECT t.root_id, a.loser_image_id FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
      WHERE {OPEN_EDGE_SQL} AND a.loser_image_id IS NOT NULL
  ),
  edges AS (
      SELECT t.root_id, a.source, a.weight, a.created_at
      FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
      WHERE {OPEN_EDGE_SQL} AND a.source = ANY(:sources) AND a.weight > 0
  ),
  by_source AS (SELECT root_id, source, sum(weight) AS n FROM edges GROUP BY root_id, source),
  agg AS (
      SELECT root_id, sum(weight) AS redrops,
             coalesce(sum(weight) FILTER (WHERE created_at >= now() - make_interval(days => :days)), 0) AS redrops_window,
             max(created_at) AS last_back
      FROM edges GROUP BY root_id
  ),
  breakdown AS (SELECT root_id, jsonb_object_agg(source, n) AS sources FROM by_source GROUP BY root_id),
  saves AS (SELECT t.root_id, count(*) AS n FROM meme_saves s JOIN tree t ON s.image_id = t.id GROUP BY t.root_id),
  """
  ```
  With impressions available add `imp AS (SELECT t.root_id, count(*) AS n, count(*) FILTER (WHERE s.shown_at >= now() - make_interval(days => :days)) AS nw FROM meme_impressions s JOIN tree t ON s.image_id = t.id GROUP BY t.root_id)`; without it use `imp AS (SELECT NULL::uuid AS root_id, NULL::bigint AS n, NULL::bigint AS nw WHERE false)`. The final select joins `agg`, `breakdown`, `images i ON i.id = agg.root_id AND i.status = 'active'`, `LEFT JOIN image_extras x ON x.image_id = i.id` with `coalesce(x.flagged, false) = false`, and `LEFT JOIN saves`/`imp`, computing `per_100` as `CASE WHEN :has_imp IS NOT TRUE THEN NULL WHEN agg.redrops_window = 0 THEN 0 WHEN coalesce(imp.nw, 0) = 0 THEN 'Infinity'::float8 ELSE agg.redrops_window * 100.0 / imp.nw END` (bind `has_imp` as a Python bool; write it as a literal `TRUE`/`FALSE` fragment chosen in Python instead if the bind form does not compile). Filter `agg.redrops_window >= :min_redrops` and, when given, `coalesce(imp.nw, 0) <= :max_impressions`. Map each name in `MEME_SORTS` to a fixed `ORDER BY` string (`per_100`: `per_100 DESC NULLS LAST, redrops_window DESC, redrops DESC`; the others: their column `DESC`, then `redrops_window DESC, redrops DESC`; with no impressions the default `per_100` becomes `redrops_window DESC, redrops DESC`, and sorting by `impressions` is then also that fallback), always ending `, i.id` for a stable order. Check `to_regclass('public.meme_impressions') IS NOT NULL` first for availability. Convert `jsonb` sources to `dict[str, int]` and `Infinity` to `float('inf')`.

- [ ] **Step 4: Run the tests again**, then fix until green. Expected: all pass.

- [ ] **Step 5: Run the whole `tests/integration` root** (the new file only reads, but the plan's rule is the whole root before each commit that touches `repository/`). Expected: green.

- [ ] **Step 6: Commit** `feat(popularity): S4 memes view of the discovery report` (only the two files above).

---

### Task 2: Queries view in the repository

**Files:**
- Modify: `repository/discovery_report.py` (add `QueryRow`, `QUERY_KINDS`, `QUERY_SORTS`, `queries_report`)
- Test: `tests/integration/test_discovery_report_queries.py`

**Interfaces:**
- Consumes: `Storage.models.SearchHistory`, `SearchHistoryTag`.
- Produces:
  - `QUERY_KINDS = ("search", "meme_text", "all")`, `QUERY_SORTS = ("times", "last", "min_results")`, `TAIL_PAGE_MINUTES = 30`.
  - `@dataclass(frozen=True) class QueryRow` with `query: str` (normalised; `""` when there is no text), `tags: str` (`category:value` pairs joined by `,`, `""` when none), `kind: str`, `times: int`, `first_at: datetime`, `last_at: datetime`, `min_results: int`, `avg_results: float`, `web: int`, `android: int`, `degraded: int`, `fallbacks: str` (distinct reasons joined by `,`).
  - `async def queries_report(session, *, kind: str = "search", max_results: int = 3, include_degraded: bool = False, since_days: int | None = None, min_times: int = 1, sort: str = "times", limit: int = 50) -> list[QueryRow]`. Raises `ValueError` for an unknown kind or sort, `max_results < 0`, `min_times < 1` or `limit < 1`.

- [ ] **Step 1: Write the failing tests** in `test_discovery_report_queries.py`. Local helper `hist(session, query, count, *, at, client="web", kind="search", tags=(), degraded=None, fallback=None)` inserting a `SearchHistory` row with an explicit `searched_at` (plus `SearchHistoryTag` rows). Use one fixed `NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)` and offsets from it. Tests:
  - `test_zero_results_is_a_failure_and_a_good_search_is_not`: counts 0 and 10 for different queries: only the first is listed.
  - `test_low_results_are_included_up_to_max_results`: counts 1, 3, 4: with the default `max_results=3` the first two are listed; `max_results=0` lists none of them (zero-only).
  - `test_queries_are_grouped_after_normalising`: `"Funny  Cat"`, `" funny cat "`, `"FUNNY CAT"` at count 0 give one row, `times == 3`, `query == "funny cat"`.
  - `test_facets_split_groups_and_a_facet_only_search_has_no_text`: same text with and without a tag are two rows; a row with `query=None` and tag `animal:cat` has `query == ""` and `tags == "animal:cat"`.
  - `test_tail_page_rule` (Review Focus 3), all with the same query:
    - count 23 at T and count 3 at T+2 min, same client: the 3 is ignored (no row).
    - count 23 at T and count 3 at T+31 min: the 3 is a failure.
    - count 23 (web) and count 3 (android) 2 minutes later: the 3 is a failure.
    - two rows of count 2 five minutes apart: both are failures (`times == 2`).
    - count 2 at T and count 3 at T+1 min: both count (a higher count is not "earlier higher" for the first; the second has the first as prior max 2 < 3, so it is not a tail).
    - a zero-result row at T+2 min after count 23 is still a failure.
  - `test_kind_filter`: a `meme_text` row and a `search` row at count 0: default lists the search row only; `kind="meme_text"` the other; `kind="all"` both.
  - `test_include_degraded`: a `meme_text` row with count 10, `degraded=True`, `fallback="timeout"` and one with count 10 and `degraded=False`: without the flag neither is listed; with `include_degraded=True, kind="meme_text"` the first is listed with `degraded == 1`, `fallbacks == "timeout"` and the second is not.
  - `test_clients_are_counted`: `web == 2`, `android == 1` for three failures of one query.
  - `test_since_days_min_times_limit_and_sorts`: rows at 3 and 40 days ago and several queries: `since_days=7` keeps the recent ones only; `min_times=2` drops singletons; `limit`; and the three sorts order as documented (times then last; last then times; min_results ascending then times).
  - `test_invalid_arguments_raise_before_any_sql`.

- [ ] **Step 2: Run to verify they fail** (`ImportError: cannot import name 'queries_report'`).

- [ ] **Step 3: Implement `queries_report`.**
  ```sql
  WITH h AS (
      SELECT s.id, s.searched_at, s.kind, s.client, s.result_count, s.degraded, s.fallback,
             lower(regexp_replace(btrim(coalesce(s.query, '')), '\s+', ' ', 'g')) AS qn,
             coalesce((SELECT string_agg(t.category || ':' || t.value, ',' ORDER BY t.category, t.value)
                       FROM search_history_tags t WHERE t.search_id = s.id), '') AS tags
      FROM search_history s
      WHERE (:kind = 'all' OR s.kind = :kind)
        AND (CAST(:since AS timestamptz) IS NULL
             OR s.searched_at >= CAST(:since AS timestamptz) - interval '30 minutes')
  ),
  f AS (
      SELECT h.*, max(h.result_count) OVER (
                 PARTITION BY h.kind, h.qn, h.tags, h.client ORDER BY h.searched_at
                 RANGE BETWEEN INTERVAL '30 minutes' PRECEDING AND CURRENT ROW) AS recent_max
      FROM h
  )
  SELECT qn, tags, kind, count(*) AS times, min(searched_at) AS first_at, max(searched_at) AS last_at,
         min(result_count) AS min_results, avg(result_count)::float8 AS avg_results,
         count(*) FILTER (WHERE client = 'web') AS web, count(*) FILTER (WHERE client = 'android') AS android,
         count(*) FILTER (WHERE degraded IS TRUE) AS degraded,
         coalesce(string_agg(DISTINCT fallback, ',') , '') AS fallbacks
  FROM f
  WHERE (CAST(:since AS timestamptz) IS NULL OR searched_at >= CAST(:since AS timestamptz))
    AND (result_count = 0
         OR (result_count <= :max_results AND NOT recent_max > result_count)
         OR (CAST(:include_degraded AS boolean) AND (degraded IS TRUE OR fallback IS NOT NULL)))
  GROUP BY qn, tags, kind
  HAVING count(*) >= :min_times
  ORDER BY <fixed fragment per sort>, qn
  LIMIT :limit
  ```
  `since` is computed in Python as `datetime.now(timezone.utc) - timedelta(days=since_days)` (or `None`); the tests pass rows whose `searched_at` is relative to the real clock for `since_days` and use the fixed `NOW` elsewhere. `string_agg(DISTINCT ...)` needs no extra ordering. Sort fragments: `times`: `times DESC, last_at DESC`; `last`: `last_at DESC, times DESC`; `min_results`: `min_results ASC, times DESC`. Note `degraded` is also a column alias in the select: keep the alias and the column reference unambiguous by qualifying (`f.degraded`) if PostgreSQL complains.

- [ ] **Step 4: Run the tests, fix until green**, then the whole `tests/integration` root. Expected: green.

- [ ] **Step 5: Commit** `feat(popularity): S4 queries view of the discovery report`.

---

### Task 3: The command, docs and wrap-up

**Files:**
- Create: `tools/discovery_report.py`, `docs/discovery-report.md`
- Modify: `CLAUDE.md` (one line after the `tools/rollout_preflight` line), `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (S4 row `done`, follow-up link `(done)`), the S4 spec (status `done`, `Plan:` link, an addendum only if a deviation occurred)
- Test: `tests/integration/test_discovery_report_cli.py` (sync tests, no module-level asyncio mark) and `tests/integration/test_discovery_report_cli_db.py` (async)

**Interfaces:**
- Consumes: `memes_report`, `queries_report`, `MemeRow`, `QueryRow`, `DEFAULT_SOURCES`, `MEME_SORTS`, `QUERY_KINDS`, `QUERY_SORTS` (Tasks 1 and 2); `tools.rollout_preflight` (`role_is_read_only(session) -> (status, detail)`, `target_check(env, url) -> (status, detail)`, `scrub(value, url)`, `OK`, `WARN`, `FAIL`); `repository.meme_impressions.window_days()`; `PopularitySource.ALL`.
- Produces: `tools.discovery_report` with `build_parser() -> argparse.ArgumentParser`, `format_table(headers: list[str], rows: list[list[str]]) -> str`, `meme_table(rows, available) -> tuple[list[str], list[list[str]]]`, `query_table(rows) -> tuple[list[str], list[list[str]]]`, `write_csv(path, headers, rows) -> None`, `async _run(url, args) -> int` and `main(argv=None) -> int` (0 success, 2 refused role, usage or connection error). Command: `python -m tools.discovery_report {memes,queries} --env {metal,general,it} [options]` with the options of spec N6 and N10 (`--csv PATH` on both).

- [ ] **Step 1: Write the failing sync tests** in `test_discovery_report_cli.py`:
  - `test_parser_defaults_and_choices`: `memes --env general` parses with the documented defaults (`min_redrops 1`, `limit 50`, `sort per_100`, `sources` default `None`); `queries --env general` defaults (`kind search`, `max_results 3`, `min_times 1`, `sort times`).
  - `test_env_is_required_and_bad_values_are_usage_errors`: no `--env` exits via `SystemExit(2)`; `--sort nope`, `--kind nope`, `--sources hash_cross_corpus,nope` are `SystemExit(2)` (assert that `main()` returns 2 or the parser exits 2, whichever the implementation uses consistently).
  - `test_meme_table_formats_inf_na_and_sources`: a `MemeRow` with `per_100=float('inf')` renders `inf`; with `per_100=None` and `available=False` renders `n/a` in the impression columns; `sources` renders `ingest_tier_a:2,recut:1` sorted by name.
  - `test_query_table_shows_no_text_for_empty_query`.
  - `test_format_table_aligns_columns_and_handles_zero_rows`: header only plus "0 rows" is produced by the caller; `format_table` with no rows returns the header and its rule.
  - `test_write_csv_writes_header_and_rows_utf8`: a row with a Cyrillic query round-trips; zero rows still writes the header (Review Focus 5).
  - `test_main_without_the_readonly_url_exits_2_and_prints_no_url`: `DATABASE_URL_READONLY` unset (monkeypatch), `main(["memes", "--env", "general"])` returns 2 and the captured stderr names the variable but contains no `postgresql`.

- [ ] **Step 2: Write the failing database tests** in `test_discovery_report_cli_db.py` (async, `db_session`; reuse `become_readonly` from `tests/integration/test_rollout_preflight.py` after all setup writes):
  - `test_a_writable_role_is_refused`: call the module's `_run_session(session, args)` (the part of `_run` after the engine is open) with the default writable test role: returns 2 and prints a line containing `can write`; nothing was queried (patch `memes_report` to raise if called).
  - `test_a_readonly_role_runs_the_memes_view`: seed one image with a `hash_cross_corpus` edge, `become_readonly`, run: returns 0, stdout contains the filename and a header line with `memes`, `general` and the window.
  - `test_the_queries_view_prints_the_query_text_to_stdout_only`: seed a failed search with text `quokka nightmare`, run `queries`: stdout contains it, `caplog.text` does not, and the stderr contains the CSV reminder only when `--csv` was passed (write to `tmp_path`).
  - `test_zero_rows_exits_0`: an empty window prints `0 rows`, returns 0.
  Structure `_run(url, args)` as: open the read-only engine, `async with AsyncSession(engine) as session: return await _run_session(session, args)`, so `_run_session` is testable on `db_session`.

- [ ] **Step 3: Run to verify they fail** (`ModuleNotFoundError: tools.discovery_report`).

- [ ] **Step 4: Implement `tools/discovery_report.py`.**
  - Parser: top-level `--env` is not allowed after the subcommand, so add `--env` to each subparser through a shared parent parser; `memes` options per spec N6 (`--window-days` default `window_days()` evaluated when parsing, `--sources` a comma string validated against `PopularitySource.ALL`, `--min-redrops`, `--max-impressions`, `--sort` choices `MEME_SORTS`, `--limit`, `--csv`); `queries` options per N10 (`--kind` choices `QUERY_KINDS`, `--max-results`, `--include-degraded`, `--since-days`, `--min-times`, `--sort` choices `QUERY_SORTS`, `--limit`, `--csv`). Reject `--limit < 1`, `--min-times < 1`, negative counts with `parser.error`.
  - `main`: read `DATABASE_URL_READONLY` (return 2 with the same wording as the preflight when missing; rewrite a `postgresql://` prefix to `postgresql+asyncpg://`); run `asyncio.run(_run(url, args))`; catch `Exception` and print `scrub(f"could not run the report: {type(exc).__name__}", url)` to stderr (the exception type only, never its message, since a SQL error echoes parameters and query text) and return 2.
  - `_run`: `create_async_engine(url, connect_args={"server_settings": {"default_transaction_read_only": "on"}})`, dispose in `finally`.
  - `_run_session`: print a WARN line to stderr when `rp.target_check(env, url)` warns (pass `url` through `args` or a parameter; `_run_session(session, args, url=None)` skips the check when `url` is None); `status, detail = await rp.role_is_read_only(session)`; on `FAIL` print the detail to stderr and return 2; otherwise call the report, print the header line (`Discovery report: memes (general, window 30 days, sources: ..., N rows)` or the queries equivalent with kind, max results, window), the table, and, with `--csv`, write the file and print `note: <path> contains search text; keep it out of the repository` to stderr for the queries view (and a plain `wrote <path>` for memes).
  - Formatting: `format_table` pads every column to its widest cell and prints a rule under the header. Timestamps as `YYYY-MM-DD HH:MM`; floats with one decimal; `inf` and `n/a` literal.

- [ ] **Step 5: Run the CLI tests until green**, then the whole `tests/integration` root, `tests/docs` and `Backend/tests` (each separately). Expected: green.

- [ ] **Step 6: Docs.**
  - `docs/discovery-report.md`: how to run it (`DATABASE_URL_READONLY` in the shell, one command per view, the options table), what each column means, how to read `per_100`, and the three caveats (impressions start when the S1 migration has run; re-drops before the popularity log existed are invisible; the tail-page rule is approximate and what it does). A worked example of the output shape with made-up numbers.
  - `CLAUDE.md`: one line after the `tools/rollout_preflight` line: `tools/discovery_report → read-only report of memes that keep coming back and of searches that found little (shell only, not a batch job): python -m tools.discovery_report {memes,queries} --env {metal,general,it} with DATABASE_URL_READONLY in the shell. See docs/discovery-report.md.`
  - Epic README: S4 row `done: 2026-11-01-s4-discovery-report-design.md`; the `Follow-ups:` entry `(done)`. S4 spec: status `done`, `Plan:` link to this file, and an addendum if any call deviated.

- [ ] **Step 7: Final checks, once:** `tests/docs`, `Backend/tests`, `tests/integration`, `batch/tests`, `tests/rules` (each separately, DATABASE_URL form for integration). Then run the tool once against nothing live: `python -m tools.discovery_report memes --env general` with `DATABASE_URL_READONLY` unset must exit 2 with the missing-variable message (do not set it, and never point the tool at a live database from this session).

- [ ] **Step 8: Commit** `feat(popularity): S4 discovery report command and docs`.
