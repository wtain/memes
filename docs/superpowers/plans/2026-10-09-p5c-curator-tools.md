# P5c Curator Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner correct a meme's profile on the meme page (save as human-verified, confirm, reset to model, with the search vector refreshed inline) and see profile coverage with per-category lists on `/admin/profiles`.

**Architecture:** Repository additions (`repository/meme_profiles.py`: read view, confirm, reset, live counts, category lists) and one rule change (human-verified profiles go stale only on a taxonomy bump); a profile service (validation, human save, inline embedding before the write, confirm, reset) with a urllib `model_digest` on the existing embedding client; two routers (`/api/meme-from-text/profiles/...`, `/api/admin/profile-coverage`); four shared schemas with regenerated types; a curator panel mounted on `MemePage` and a coverage page, both reusing the P5 chip components and taxonomy hook.

**Tech Stack:** FastAPI + SQLAlchemy async (Postgres/pgvector), the generated Pydantic/TypeScript/Kotlin types, React 19 + React Compiler lint rules + react-router 7 (data router), vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p5c-curator-tools-design.md` (read it first). Inherited: P2 (`repository/meme_profiles.py`, `batch/utils/meme_profile_selection.py`), P4 (`retrieval/embed.py`), P5 and P5b (reply components, the 422 handler, router/service patterns in `Backend/app/api/meme_text_ratings.py`).

## Global Constraints

- Python is `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe` (a worktree has no venv). Run each pytest root as its own command (`Backend` from `Backend/`, `tests/retrieval`, `tests/evaluation`, `tests/taxonomy`, `batch/tests`, the whole `tests/integration`). **Never `pip install`, never `pnpm install`**; `Frontend/memes-frontend/node_modules` in the worktree is a junction the controller creates (git-ignored); if it is missing stop and report. Frontend commands run from `Frontend/memes-frontend/` with `pnpm exec`.
- **Database:** only the dedicated test database `postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`, given on the command line for integration tests (Tasks 1 and 2 only). Never any other URL, never `environments/.env.*`, never `alembic` (this feature needs no migration), never bind a dev server or any port. No task may touch the metal/general/it databases; the controller does all live steps.
- Work only inside your own worktree (earlier subagents wrote into the main checkout by mistake): before every commit run `git status` and `git rev-parse --show-toplevel` and confirm both point at the worktree.
- The profile situation text and the user's messages are never logged; log lines carry only the image id and the outcome. Error bodies carry fixed texts only (a validation message may name an axis or a taxonomy key, never the free text).
- The React app renders in `StrictMode` and the ESLint ruleset includes the React Compiler rules: every effect must survive a double mount, no ref reads/writes during render, no synchronous `setState`/`dispatch` in an effect body, exhaustive deps. Restructure (never disable a rule; an `exhaustive-deps` disable only with a one-line justification when the omitted dependency is provably stable).
- Frontend gates before every frontend commit, with real output pasted in the report: `pnpm exec tsc -b; echo "exit $?"` (silent on success), `pnpm exec eslint src/ --max-warnings 0; echo "exit $?"`, the whole `pnpm exec vitest run`, and (Tasks 5 and 6) `pnpm exec vite build`. The controller re-runs every gate after each task.
- Regenerating types (Task 3): Python from `Backend/` with `datamodel-codegen` (command in `documents/generation.md`, executable `/h/workspace_sandbox/memes/.venv311/Scripts/datamodel-codegen.exe`), TypeScript with `bash generate-types.sh` from `Frontend/`, Kotlin with `python AndroidClient/scripts/generate_dtos.py` from the worktree root. Commit only real changes (revert timestamp-only churn in unrelated generated files with `git checkout -- <path>`). Never hand-edit generated files. The new schemas set `"additionalProperties": false` on every object they define, so the generated TypeScript types are exact (no index signature).
- Preserve each existing file's line endings (use the Edit tool; check `git ls-files --eol <file>`); new files may be LF. Do not round-trip repo text through PowerShell `Get/Set-Content`.
- Constants: situation at most 160 characters (the taxonomy's `situation` axis `max_chars`), use-when at most 300 (`ai.meme_profile_extractor.MAX_USE_WHEN_CHARS`), a human value's confidence is `1.0`, the failure pipeline name is `"meme_profile"` (`batch.utils.meme_profile_config.PIPELINE`), batch run kinds `build_meme_profiles` and `build_meme_profile_embeddings`, categories `without_profile | needs_review | stale_taxonomy | failed | human_verified | pending_vector`, vector status `fresh | pending | none`.
- Commit messages end with these two trailer lines, exactly (Claude Sonnet 5.5, never another model name):
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01WUvgSm19vfxX68CBg4XvxY`.

## File Structure

| Path | Responsibility |
|---|---|
| `repository/meme_profiles.py` (modify), `batch/utils/meme_profile_selection.py` (modify) | Read view, confirm, reset, live counts, category lists; human-verified staleness rule |
| `retrieval/embed.py` (modify), `environments/settings.yaml` (modify) | `model_digest` on the urllib client; `profile_editor.embed_timeout_seconds` |
| `Backend/app/services/meme_profile_service.py` | Validation, human save with inline vector, confirm, reset, response building |
| `Backend/app/services/profile_coverage_service.py` | Live counts, last runs, category pages |
| `Backend/app/api/meme_profiles.py`, `Backend/app/api/admin_profiles.py`, `Backend/app/main.py` (modify), `backend_api.md` (modify) | Routers, wiring, docs |
| `shared/schemas/curatedprofile|curatedprofilesaverequest|profilecoverage|profilecoverageimages.schema.json`, `all.schema.json` (modify) + regenerated trees | Public schemas |
| `Frontend/memes-frontend/src/curator/types.ts`, `fixtures.ts`, `draft.ts`, `useProfileEditor.ts`, `useUnsavedGuard.ts`, `useProfileCoverage.ts` | Curator logic (pure helpers and hooks) |
| `src/components/curator/ProfileEditor.tsx`, `src/pages/ProfileCoveragePage.tsx` | UI |
| `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/api/errors.ts`, `src/test/mockApi.ts`, `src/pages/MemePage.tsx`, `src/app/router.tsx`, `src/app/AppLayout.tsx` (modify) | API layer, mounting, route, nav |

## Deviations decided while planning

1. The new situation vector is computed **before** the database write inside the same request and stored in the **same** transaction as the profile (the spec said a second transaction). No row lock is held during the Ollama call, a failed embedding still leaves the profile saved with `vector: "pending"`, and a crash cannot leave a vector without its profile.
2. The batch job keeps `ai.ollama_embed.OllamaEmbedder`; the backend gets `OllamaEmbedClient.model_digest` (urllib, `GET /api/tags`) instead of extracting a shared class. Both read the same Ollama field (`digest`) and the same configured model name, so the stored `(model_name, model_digest)` agree; the live check verifies this against a vector written by the job.
3. The situation limit is the taxonomy's 160 characters (the spec text said 600); the spec is corrected in the same commit as this plan.
4. The "failed" category counts `failed` rows of the profile pipeline on active images whether or not the image also has a profile.

---

### Task 1: Repository additions and the human-verified staleness rule

**Files:**
- Modify: `repository/meme_profiles.py`, `batch/utils/meme_profile_selection.py`, `batch/tests/test_meme_profile_selection.py`, `tests/integration/test_meme_profiles_repository.py` (one new case)
- Create: `tests/integration/test_meme_profiles_curator_repository.py`

**Interfaces:**
- Produces in `repository/meme_profiles.py`: constants `PIPELINE = "meme_profile"`, `CATEGORIES = ("without_profile", "needs_review", "stale_taxonomy", "failed", "human_verified", "pending_vector")`; frozen dataclass `ProfileView(image_id, taxonomy_version, human_verified, situation_text, situation_confidence, use_when, values: dict[str, list[dict]], extracted_at: str, updated_at: str)` (`values` always has the four axes, each list ordered by confidence desc then key, items `{"key","confidence","source"}`); `MemeProfileRepository` methods `async image_is_active(image_id) -> bool`, `async get_view(image_id) -> ProfileView | None`, `async vector_status(image_id, model_name) -> str` (`"none"` when the profile is missing or its situation is blank, `"fresh"` when a vector row with that `model_name` exists, else `"pending"`), `async confirm(image_id, taxonomy_version) -> bool`, `async reset(image_id) -> bool`, `async live_counts(taxonomy_version, model_name) -> dict` (keys `active, profiled, without_profile, needs_review, stale_taxonomy, failed, human_verified, pending_vector`), `async list_category(category, taxonomy_version, model_name, after: uuid.UUID | None, limit: int) -> tuple[list[tuple[uuid.UUID, bool]], int]` (rows ordered by image id, `(id, human_verified)`, plus the category total).
- Changes: a human-verified profile is stale only when its taxonomy version differs from the current one, in `select_work` and in `coverage()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_meme_profiles_curator_repository.py`:

```python
"""Integration tests for the curator additions of repository/meme_profiles.py (P5c). Needs the test database."""
import uuid

import pytest
from sqlalchemy import select

from repository import meme_profiles
from repository.meme_profiles import CATEGORIES, PIPELINE, MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import (
    PROFILE_EMBEDDING_DIM, Image, ImageProcessingStatus, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue,
)

CURRENT = 2
MODEL = "bge-large"


async def _image(session, status="active") -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


def _meta(**overrides) -> ProfileMeta:
    values = dict(taxonomy_version=CURRENT, extractor_model="qwen2", extractor_prompt_version="p2-test",
                  inputs_fingerprint="f" * 64, human_verified=False)
    values.update(overrides)
    return ProfileMeta(**values)


def _data(situation="plans cancelled", **axes) -> ProfileData:
    values = {axis: [{"key": key, "confidence": confidence, "source": "model"} for key, confidence in pairs]
              for axis, pairs in axes.items()}
    return ProfileData(situation_text=situation, situation_confidence=None, use_when="Use when plans fall through.",
                       values=values)


async def _vector(repo, image_id, model=MODEL):
    await repo.save_situation_embedding(image_id, [0.1] * PROFILE_EMBEDDING_DIM, model, "digest")


def test_the_pipeline_name_matches_the_batch_job():
    from batch.utils.meme_profile_config import PIPELINE as BATCH_PIPELINE
    assert PIPELINE == BATCH_PIPELINE


@pytest.mark.asyncio(loop_scope="session")
async def test_get_view_returns_all_four_axes_ordered_by_confidence(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(emotion=[("pride", 0.4), ("joy", 0.9), ("amusement", 0.9)]), _meta())
    await db_session.flush()

    view = await repo.get_view(image.id)

    assert set(view.values) == {"emotion", "stance", "topic", "tone"}
    assert [item["key"] for item in view.values["emotion"]] == ["amusement", "joy", "pride"]
    assert view.values["stance"] == [] and view.human_verified is False and view.taxonomy_version == CURRENT
    assert view.situation_text == "plans cancelled" and isinstance(view.extracted_at, str)
    assert await repo.get_view(uuid.uuid4()) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_image_is_active_only_for_active_images(db_session):
    active, pending = await _image(db_session), await _image(db_session, status="pending")
    repo = MemeProfileRepository(db_session)
    assert await repo.image_is_active(active.id) is True
    assert await repo.image_is_active(pending.id) is False
    assert await repo.image_is_active(uuid.uuid4()) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_vector_status_covers_none_pending_and_fresh(db_session):
    blank, with_text = await _image(db_session), await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(blank.id, _data(situation=None), _meta())
    await repo.save_profile(with_text.id, _data(), _meta())
    await db_session.flush()

    assert await repo.vector_status(uuid.uuid4(), MODEL) == "none"
    assert await repo.vector_status(blank.id, MODEL) == "none"
    assert await repo.vector_status(with_text.id, MODEL) == "pending"
    await _vector(repo, with_text.id)
    await db_session.flush()
    assert await repo.vector_status(with_text.id, MODEL) == "fresh"
    assert await repo.vector_status(with_text.id, "another-model") == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_confirm_marks_verified_and_keeps_values_sources_and_vector(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(emotion=[("joy", 0.9)]), _meta(taxonomy_version=1))
    await _vector(repo, image.id)
    await db_session.flush()

    assert await repo.confirm(image.id, CURRENT) is True
    await db_session.flush()

    view = await repo.get_view(image.id)
    assert view.human_verified is True and view.taxonomy_version == CURRENT
    assert view.values["emotion"] == [{"key": "joy", "confidence": 0.9, "source": "model"}]
    assert await repo.vector_status(image.id, MODEL) == "fresh"
    assert await repo.confirm(uuid.uuid4(), CURRENT) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_deletes_the_profile_values_vector_and_only_this_pipelines_failure(db_session):
    image, other = await _image(db_session), await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(image.id, _data(emotion=[("joy", 1.0)]), _meta(human_verified=True))
    await repo.save_profile(other.id, _data(emotion=[("pride", 1.0)]), _meta())
    await _vector(repo, image.id)
    db_session.add_all([
        ImageProcessingStatus(image_id=image.id, pipeline=PIPELINE, status="failed", error_message="x"),
        ImageProcessingStatus(image_id=image.id, pipeline="other_pipeline", status="done"),
    ])
    await db_session.flush()

    assert await repo.reset(image.id) is True
    await db_session.flush()
    db_session.expire_all()

    assert await repo.get_view(image.id) is None
    assert (await db_session.execute(select(MemeProfileValue).where(MemeProfileValue.image_id == image.id))).first() is None
    assert (await db_session.execute(
        select(MemeProfileSituationEmbedding).where(MemeProfileSituationEmbedding.image_id == image.id))).first() is None
    left = (await db_session.execute(
        select(ImageProcessingStatus.pipeline).where(ImageProcessingStatus.image_id == image.id))).scalars().all()
    assert left == ["other_pipeline"]
    assert await repo.get_view(other.id) is not None
    assert await repo.reset(image.id) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_saving_a_human_profile_keeps_the_vector_only_when_the_situation_is_unchanged(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(situation="plans cancelled"), _meta())
    await _vector(repo, image.id)
    await db_session.flush()

    await repo.save_human_profile(image.id, _data(situation="plans cancelled", emotion=[("joy", 1.0)]), _meta(human_verified=True))
    await db_session.flush()
    assert await repo.vector_status(image.id, MODEL) == "fresh"

    await repo.save_human_profile(image.id, _data(situation="a different situation"), _meta(human_verified=True))
    await db_session.flush()
    assert await repo.vector_status(image.id, MODEL) == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_batch_write_cannot_overwrite_a_human_save(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(image.id, _data(emotion=[("joy", 1.0)]), _meta(human_verified=True))
    await db_session.flush()

    assert await repo.save_profile(image.id, _data(emotion=[("pride", 0.5)]), _meta()) is False
    assert (await repo.get_view(image.id)).values["emotion"][0]["key"] == "joy"


async def _corpus(session):
    """One image per category, plus decoys that must be counted nowhere (a pending image)."""
    repo = MemeProfileRepository(session)
    rows = {name: await _image(session) for name in
            ("without", "fresh", "stale", "human_stale", "human_fresh", "failed", "no_text")}
    await _image(session, status="pending")                                   # not active: counted nowhere
    await repo.save_profile(rows["fresh"].id, _data(), _meta())
    await _vector(repo, rows["fresh"].id)
    await repo.save_profile(rows["stale"].id, _data(), _meta(taxonomy_version=1))
    await repo.save_human_profile(rows["human_stale"].id, _data(), _meta(taxonomy_version=1, human_verified=True))
    await _vector(repo, rows["human_stale"].id)
    await repo.save_human_profile(rows["human_fresh"].id, _data(), _meta(human_verified=True))
    await _vector(repo, rows["human_fresh"].id)
    await repo.save_profile(rows["no_text"].id, _data(situation=None), _meta())
    session.add(ImageProcessingStatus(image_id=rows["failed"].id, pipeline=PIPELINE, status="failed", error_message="x"))
    await session.flush()
    return repo, rows


@pytest.mark.asyncio(loop_scope="session")
async def test_live_counts_and_category_lists_agree(db_session):
    repo, rows = await _corpus(db_session)

    counts = await repo.live_counts(CURRENT, MODEL)

    assert counts == {"active": 7, "profiled": 5, "without_profile": 2, "needs_review": 1, "stale_taxonomy": 1,
                      "failed": 1, "human_verified": 2, "pending_vector": 1}
    expected = {
        "without_profile": {rows["without"].id, rows["failed"].id},
        "needs_review": {rows["human_stale"].id},
        "stale_taxonomy": {rows["stale"].id},
        "failed": {rows["failed"].id},
        "human_verified": {rows["human_stale"].id, rows["human_fresh"].id},
        "pending_vector": {rows["stale"].id},
    }
    for category in CATEGORIES:
        listed, total = await repo.list_category(category, CURRENT, MODEL, None, 100)
        assert total == counts[category], category
        assert {image_id for image_id, _ in listed} == expected[category], category
    listed, _ = await repo.list_category("human_verified", CURRENT, MODEL, None, 100)
    assert all(human for _, human in listed)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_vector_from_another_model_counts_as_pending(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(), _meta())
    await _vector(repo, image.id, model="old-model")
    await db_session.flush()

    listed, total = await repo.list_category("pending_vector", CURRENT, MODEL, None, 10)

    assert total == 1 and [image_id for image_id, _ in listed] == [image.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_category_pages_by_image_id(db_session):
    repo = MemeProfileRepository(db_session)
    created = [await _image(db_session) for _ in range(5)]

    first, total = await repo.list_category("without_profile", CURRENT, MODEL, None, 2)
    second, _ = await repo.list_category("without_profile", CURRENT, MODEL, first[-1][0], 2)
    last, _ = await repo.list_category("without_profile", CURRENT, MODEL, second[-1][0], 2)

    assert total == 5
    ids = [image_id for image_id, _ in first + second + last]
    assert len(ids) == 5 and ids == sorted(ids, key=str) and set(ids) == {image.id for image in created}


def test_the_repository_never_commits():
    import inspect
    assert "commit(" not in inspect.getsource(meme_profiles)
```

Add to `batch/tests/test_meme_profile_selection.py`:

```python
def test_a_human_verified_profile_is_not_stale_when_only_prompt_model_or_inputs_changed():
    candidate = _candidate()
    for override in (dict(extractor_prompt_version="p2-old"), dict(extractor_model="human"),
                     dict(inputs_fingerprint="other")):
        result = _select([candidate], {candidate.image_id: _meta(candidate, human_verified=True, **override)})
        assert result.work == [] and result.stale_ids == frozenset() and result.needs_review_ids == frozenset(), override


def test_a_human_verified_profile_from_an_older_taxonomy_needs_review_and_is_never_selected():
    candidate = _candidate()
    result = _select([candidate], {candidate.image_id: _meta(candidate, human_verified=True, taxonomy_version=1,
                                                              extractor_model="human", inputs_fingerprint="human")})
    assert result.work == []
    assert result.stale_ids == frozenset({candidate.image_id})
    assert result.needs_review_ids == frozenset({candidate.image_id})
```

Add to `tests/integration/test_meme_profiles_repository.py` (next to the existing coverage test):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_coverage_counts_a_human_profile_stale_only_for_the_taxonomy(db_session):
    human_other_prompt = await _insert_image(db_session)
    human_old_taxonomy = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(human_other_prompt.id, _data(), _meta(extractor_prompt_version="human"))
    await repo.save_human_profile(human_old_taxonomy.id, _data(), _meta(taxonomy_version=1, extractor_prompt_version="human"))
    await db_session.flush()

    coverage = await repo.coverage(taxonomy_version=2, prompt_version="p2-now")

    assert (coverage["stale"], coverage["needs_review"], coverage["human_verified"]) == (1, 1, 2)
```

- [ ] **Step 2: Run to see them fail**

`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_meme_profiles_curator_repository.py tests/integration/test_meme_profiles_repository.py -q` (ERROR or FAIL: missing `CATEGORIES`/methods) and `/h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest batch/tests/test_meme_profile_selection.py -q` (the two new tests FAIL).

- [ ] **Step 3: Implement the rule change**

In `batch/utils/meme_profile_selection.py` replace the staleness block inside `select_work` with:

```python
        if meta is not None:
            if meta.human_verified:
                # A person's correction does not depend on the prompt, the model or the extraction inputs:
                # it only goes stale when the vocabulary it was made against changes (P5c spec, decision 2).
                is_stale = meta.taxonomy_version != taxonomy_version
            else:
                is_stale = (
                    meta.taxonomy_version != taxonomy_version
                    or meta.extractor_prompt_version != prompt_version
                    or meta.extractor_model != model
                    or meta.inputs_fingerprint != candidate.fingerprint
                )
            if not is_stale:
                continue
            stale.add(candidate.image_id)
            if meta.human_verified:
                needs_review.add(candidate.image_id)
                continue
```

and extend the docstring sentence "Human-verified profiles are never selected: when stale they are reported as needing review." with "A human-verified profile is stale only when its taxonomy version differs." In `repository/meme_profiles.py::coverage` replace the loop body with:

```python
        for image_id, version, prompt, human_verified in rows:
            if human_verified:
                is_stale = version != taxonomy_version
            else:
                is_stale = version != taxonomy_version or prompt != prompt_version or image_id in stale_image_ids
            stale += is_stale
            human += human_verified
            needs_review += is_stale and human_verified
```

- [ ] **Step 4: Implement the repository additions** in `repository/meme_profiles.py` (add the imports `from dataclasses import dataclass` is already there; add `from sqlalchemy import exists, or_` if missing, and `ImageProcessingStatus` to the `Storage.models` import):

```python
PIPELINE = "meme_profile"   # equals batch.utils.meme_profile_config.PIPELINE (the backend image has no batch/); a test pins it
CATEGORIES = ("without_profile", "needs_review", "stale_taxonomy", "failed", "human_verified", "pending_vector")
AXES = ("emotion", "stance", "topic", "tone")


@dataclass(frozen=True)
class ProfileView:
    image_id: uuid.UUID
    taxonomy_version: int
    human_verified: bool
    situation_text: str | None
    situation_confidence: float | None
    use_when: str | None
    values: dict[str, list[dict]]
    extracted_at: str
    updated_at: str
```

and inside `MemeProfileRepository`:

```python
    async def image_is_active(self, image_id: uuid.UUID) -> bool:
        found = await self.session.execute(select(Image.id).where(Image.id == image_id, Image.status == "active"))
        return found.first() is not None

    async def get_view(self, image_id: uuid.UUID) -> "ProfileView | None":
        row = (await self.session.execute(
            select(MemeProfile).where(MemeProfile.image_id == image_id).execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if row is None:
            return None
        items = (await self.session.execute(
            select(MemeProfileValue.axis, MemeProfileValue.value_key, MemeProfileValue.confidence,
                   MemeProfileValue.source)
            .where(MemeProfileValue.image_id == image_id)
            .order_by(MemeProfileValue.confidence.desc(), MemeProfileValue.value_key)
        )).all()
        values: dict[str, list[dict]] = {axis: [] for axis in AXES}
        for axis, key, confidence, source in items:
            values[axis].append({"key": key, "confidence": confidence, "source": source})
        return ProfileView(
            image_id=row.image_id, taxonomy_version=row.taxonomy_version, human_verified=bool(row.human_verified),
            situation_text=row.situation_text, situation_confidence=row.situation_confidence, use_when=row.use_when,
            values=values, extracted_at=row.extracted_at.isoformat(), updated_at=row.updated_at.isoformat())

    async def vector_status(self, image_id: uuid.UUID, model_name: str) -> str:
        situation = (await self.session.execute(
            select(MemeProfile.situation_text).where(MemeProfile.image_id == image_id))).scalar_one_or_none()
        if situation is None or not situation.strip():
            return "none"
        stored = (await self.session.execute(
            select(MemeProfileSituationEmbedding.model_name)
            .where(MemeProfileSituationEmbedding.image_id == image_id))).scalar_one_or_none()
        return "fresh" if stored == model_name else "pending"

    async def confirm(self, image_id: uuid.UUID, taxonomy_version: int) -> bool:
        """Mark the profile human-verified without touching its values, their sources or its vector."""
        result = await self.session.execute(
            update(MemeProfile).where(MemeProfile.image_id == image_id)
            .values(human_verified=True, taxonomy_version=taxonomy_version, updated_at=func.now()))
        return result.rowcount > 0

    async def reset(self, image_id: uuid.UUID) -> bool:
        """Delete the profile (values and vector cascade) and this pipeline's failure record, so the next
        build_meme_profiles run re-extracts the image."""
        deleted = await self.session.execute(delete(MemeProfile).where(MemeProfile.image_id == image_id))
        await self.session.execute(delete(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image_id, ImageProcessingStatus.pipeline == PIPELINE))
        return deleted.rowcount > 0

    def _category_query(self, category: str, taxonomy_version: int, model_name: str):
        base = (select(Image.id, func.coalesce(MemeProfile.human_verified, False).label("human"))
                .select_from(Image).outerjoin(MemeProfile, MemeProfile.image_id == Image.id)
                .where(Image.status == "active"))
        if category == "without_profile":
            return base.where(MemeProfile.image_id.is_(None))
        if category == "needs_review":
            return base.where(MemeProfile.human_verified.is_(True), MemeProfile.taxonomy_version != taxonomy_version)
        if category == "stale_taxonomy":
            return base.where(MemeProfile.human_verified.is_(False), MemeProfile.taxonomy_version != taxonomy_version)
        if category == "human_verified":
            return base.where(MemeProfile.human_verified.is_(True))
        if category == "failed":
            return base.where(exists().where(
                ImageProcessingStatus.image_id == Image.id, ImageProcessingStatus.pipeline == PIPELINE,
                ImageProcessingStatus.status == "failed"))
        if category == "pending_vector":
            vector = MemeProfileSituationEmbedding
            return (base.outerjoin(vector, vector.image_id == Image.id)
                    .where(MemeProfile.situation_text.is_not(None),
                           func.length(func.trim(MemeProfile.situation_text)) > 0,
                           or_(vector.image_id.is_(None), vector.model_name != model_name)))
        raise ValueError(f"unknown category {category!r}")

    async def _count_category(self, category: str, taxonomy_version: int, model_name: str) -> int:
        query = self._category_query(category, taxonomy_version, model_name).subquery()
        return (await self.session.execute(select(func.count()).select_from(query))).scalar_one()

    async def live_counts(self, taxonomy_version: int, model_name: str) -> dict:
        active = (await self.session.execute(
            select(func.count()).select_from(Image).where(Image.status == "active"))).scalar_one()
        counts = {category: await self._count_category(category, taxonomy_version, model_name)
                  for category in CATEGORIES}
        return {"active": active, "profiled": active - counts["without_profile"], **counts}

    async def list_category(self, category: str, taxonomy_version: int, model_name: str,
                            after: uuid.UUID | None, limit: int) -> tuple[list[tuple[uuid.UUID, bool]], int]:
        total = await self._count_category(category, taxonomy_version, model_name)
        page = self._category_query(category, taxonomy_version, model_name).order_by(Image.id)
        if after is not None:
            page = page.where(Image.id > after)
        rows = (await self.session.execute(page.limit(limit))).all()
        return [(image_id, bool(human)) for image_id, human in rows], total
```

- [ ] **Step 5: Run the tests**

Each as its own command: the new and existing profile repository tests (`DATABASE_URL=... pytest tests/integration/test_meme_profiles_curator_repository.py tests/integration/test_meme_profiles_repository.py -q`), `pytest batch/tests -q`, then the WHOLE `tests/integration` root (`repository/meme_profiles.py` is shared with the batch jobs), and `pytest tests/retrieval -q`.

- [ ] **Step 6: Commit**

```bash
git add repository/meme_profiles.py batch/utils/meme_profile_selection.py batch/tests/test_meme_profile_selection.py tests/integration
git commit -m "feat(profiles): add the curator read/confirm/reset/coverage queries and human-verified staleness"
```

---

### Task 2: Embedding digest, settings key and the profile service

**Files:**
- Modify: `retrieval/embed.py`, `tests/retrieval/test_embed.py` (find the real name: `ls tests/retrieval | grep embed`), `environments/settings.yaml`
- Create: `Backend/app/services/meme_profile_service.py`, `Backend/tests/test_meme_profile_service.py`

**Interfaces:**
- Consumes: Task 1's repository methods; `taxonomy.load()` (`.version`, `.has_key(axis, key)`), `taxonomy.validate.validate_profile`, `repository.meme_profiles.ProfileData/ProfileMeta`, `retrieval.embed.EmbedError`.
- Produces: `OllamaEmbedClient.model_digest(model: str, timeout: float) -> str` (raises `EmbedUnavailable`/`EmbedTimeout`/`EmbedBadResponse`); setting `profile_editor.embed_timeout_seconds` (default read as `settings.get("profile_editor.embed_timeout_seconds", 8.0)`); in the service module: `ProfileValidationError(ValueError)`, `UnknownImageError(LookupError)`, `ProfileConflictError(Exception)` (all carry fixed messages), `MemeProfileService(repository, embedder, *, embedding_model: str, embed_timeout: float, taxonomy=None)` with `async get(image_id: str)`, `async save(image_id: str, request)`, `async confirm(image_id: str)`, `async reset(image_id: str) -> None`; `get/save/confirm` return the generated `CuratedProfile` (Task 3 creates the schema: until then the service builds a plain dict via `_profile_dict(...)` and the router task wraps it, see Step 4).

- [ ] **Step 1: Failing tests for `model_digest`** (add to the embed test file; read it for the helper that fakes `urllib.request.urlopen`, and reuse it)

```python
def test_model_digest_reads_the_matching_model_from_api_tags(fake_urlopen):
    fake_urlopen.respond(json.dumps({"models": [
        {"model": "other:latest", "digest": "aaa"}, {"model": "bge-large:latest", "digest": "b3d71c92"}]}))
    client = OllamaEmbedClient("http://ollama:11434", 1024)

    assert client.model_digest("bge-large", 3.0) == "b3d71c92"
    assert fake_urlopen.last_url == "http://ollama:11434/api/tags"


def test_model_digest_accepts_a_fully_qualified_name(fake_urlopen):
    fake_urlopen.respond(json.dumps({"models": [{"model": "bge-large", "digest": "d1"}]}))
    assert OllamaEmbedClient("http://ollama:11434").model_digest("bge-large", 3.0) == "d1"


def test_model_digest_errors_are_typed_and_carry_no_text(fake_urlopen):
    client = OllamaEmbedClient("http://ollama:11434")
    fake_urlopen.respond(json.dumps({"models": []}))
    with pytest.raises(EmbedUnavailable):
        client.model_digest("bge-large", 3.0)
    fake_urlopen.respond("not json")
    with pytest.raises(EmbedBadResponse):
        client.model_digest("bge-large", 3.0)
    with pytest.raises(EmbedTimeout):
        client.model_digest("bge-large", 0)
```

(Adapt to the file's real helper: if it patches `urllib.request.urlopen` with `monkeypatch`, write an equivalent small fake. Keep all existing embed tests green.)

- [ ] **Step 2: Implement `model_digest`** in `retrieval/embed.py`. Factor the shared request/error mapping out of `embed` into a private `_send(self, request, timeout) -> bytes` that contains the existing `try/except` ladder verbatim (the 404 text stays the same), make `embed` call it, and add:

```python
    def model_digest(self, model: str, timeout: float) -> str:
        """The digest of an Ollama model, as `ollama list` reports it (GET /api/tags)."""
        if timeout <= 0:
            raise EmbedTimeout("no time left for the call")
        request = urllib.request.Request(self._base + "/api/tags", method="GET")
        payload = self._send(request, timeout)
        try:
            models = json.loads(payload)["models"]
            wanted = {model, f"{model}:latest"}
            for entry in models:
                if entry.get("model") in wanted or entry.get("name") in wanted:
                    return str(entry["digest"])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise EmbedBadResponse("the Ollama response has no model list") from None
        raise EmbedUnavailable("the embedding model is not installed in Ollama")
```

and store `self._base = base_url.rstrip("/")` in `__init__` (keep `self._url = self._base + "/api/embed"`).

- [ ] **Step 3: Settings key.** In `environments/settings.yaml`, directly above the `parse:` block (find it; preserve line endings), add:

```yaml
profile_editor:
  # Meme-profile curator (epic meme-from-text, P5c). Time budget of the inline situation-vector refresh on save;
  # when it is exceeded the profile is still saved and the vector is refreshed by build_meme_profile_embeddings.
  embed_timeout_seconds: 8
```

- [ ] **Step 4: Failing service tests.** Create `Backend/tests/test_meme_profile_service.py`:

```python
import uuid

import pytest

from Backend.app.services.meme_profile_service import (
    MAX_USE_WHEN_CHARS, MemeProfileService, ProfileConflictError, ProfileValidationError, UnknownImageError,
)
from repository.meme_profiles import ProfileView
from retrieval.embed import EmbedTimeout, EmbedUnavailable
from taxonomy import load

TAX = load()
IMAGE = str(uuid.uuid4())
EMPTY_VALUES = {"emotion": [], "stance": [], "topic": [], "tone": []}


class FakeRepo:
    def __init__(self, view=None, active=True, vector="pending"):
        self.view, self.active, self.vector = view, active, vector
        self.saved, self.vectors, self.confirmed, self.reset_calls = [], [], [], []

    async def image_is_active(self, image_id):
        return self.active

    async def get_view(self, image_id):
        return self.view

    async def vector_status(self, image_id, model_name):
        return self.vector

    async def save_human_profile(self, image_id, data, meta):
        self.saved.append((data, meta))
        values = {axis: [{"key": i["key"], "confidence": 1.0, "source": "human"} for i in items]
                  for axis, items in data.values.items()}
        self.view = ProfileView(image_id, meta.taxonomy_version, True, data.situation_text, None, data.use_when,
                                values, "2026-10-09T10:00:00", "2026-10-09T10:00:00")

    async def save_situation_embedding(self, image_id, vector, model_name, model_digest):
        self.vectors.append((vector, model_name, model_digest))
        self.vector = "fresh"

    async def confirm(self, image_id, taxonomy_version):
        self.confirmed.append(taxonomy_version)
        return True

    async def reset(self, image_id):
        self.reset_calls.append(image_id)
        return True


class FakeEmbedder:
    def __init__(self, error=None, digest_error=None):
        self.error, self.digest_error, self.embedded, self.digests = error, digest_error, [], []

    def model_digest(self, model, timeout):
        self.digests.append(model)
        if self.digest_error:
            raise self.digest_error
        return "digest-1"

    def embed(self, model, texts, timeout):
        self.embedded.append(list(texts))
        if self.error:
            raise self.error
        return [[0.5] * 1024 for _ in texts]


def _view(**overrides):
    values = dict(image_id=uuid.UUID(IMAGE), taxonomy_version=TAX.version, human_verified=False,
                  situation_text="old situation", situation_confidence=None, use_when=None,
                  values={**EMPTY_VALUES, "emotion": [{"key": "joy", "confidence": 0.9, "source": "model"}]},
                  extracted_at="2026-10-09T09:00:00", updated_at="2026-10-09T09:00:00")
    values.update(overrides)
    return ProfileView(**values)


def _request(**overrides):
    body = {"situation": "a new situation", "useWhen": "Use when it fits.",
            "values": {"emotion": ["joy"], "stance": [], "topic": ["work"], "tone": []}}
    body.update(overrides)
    return type("Req", (), {
        "situation": body["situation"], "useWhen": body["useWhen"],
        "values": type("Vals", (), body["values"])()})()


def _service(repo=None, embedder=None, **kwargs):
    repo = repo or FakeRepo()
    embedder = embedder or FakeEmbedder()
    return MemeProfileService(repo, embedder, embedding_model="bge-large", embed_timeout=5.0, taxonomy=TAX), repo, embedder


async def test_save_writes_a_human_profile_and_stores_the_fresh_vector():
    service, repo, embedder = _service()

    response = await service.save(IMAGE, _request())

    data, meta = repo.saved[0]
    assert (meta.human_verified, meta.extractor_model, meta.taxonomy_version) == (True, "human", TAX.version)
    assert data.situation_text == "a new situation" and data.use_when == "Use when it fits."
    assert [(i["key"], i["confidence"], i["source"]) for i in data.values["topic"]] == [("work", 1.0, "human")]
    assert embedder.embedded == [["a new situation"]] and repo.vectors[0][1:] == ("bge-large", "digest-1")
    assert response["vector"] == "fresh" and response["humanVerified"] is True and response["exists"] is True


async def test_an_unchanged_situation_makes_no_embedding_call():
    service, repo, embedder = _service(FakeRepo(view=_view(situation_text="a new situation"), vector="fresh"))

    response = await service.save(IMAGE, _request())

    assert embedder.embedded == [] and repo.vectors == [] and response["vector"] == "fresh"


@pytest.mark.parametrize("error", [EmbedTimeout("t"), EmbedUnavailable("u"), RuntimeError("boom")])
async def test_a_failed_embedding_still_saves_and_reports_pending(error):
    service, repo, _ = _service(embedder=FakeEmbedder(error=error))

    response = await service.save(IMAGE, _request())

    assert len(repo.saved) == 1 and repo.vectors == [] and response["vector"] == "pending"


async def test_a_digest_failure_also_degrades_to_pending():
    service, repo, _ = _service(embedder=FakeEmbedder(digest_error=EmbedUnavailable("u")))
    assert (await service.save(IMAGE, _request()))["vector"] == "pending" and repo.vectors == []


async def test_a_blank_situation_has_no_vector_and_no_call():
    service, repo, embedder = _service(FakeRepo(vector="none"))
    response = await service.save(IMAGE, _request(situation="   "))
    assert embedder.embedded == [] and response["vector"] == "none" and repo.saved[0][0].situation_text is None


@pytest.mark.parametrize("bad", [
    dict(values={"emotion": ["not-a-key"], "stance": [], "topic": [], "tone": []}),
    dict(values={"emotion": ["joy", "amusement", "pride", "relief"], "stance": [], "topic": [], "tone": []}),
    dict(situation="x" * 161),
    dict(useWhen="y" * (MAX_USE_WHEN_CHARS + 1)),
])
async def test_invalid_profiles_are_rejected_before_anything_is_written(bad):
    service, repo, embedder = _service()
    with pytest.raises(ProfileValidationError) as error:
        await service.save(IMAGE, _request(**bad))
    assert repo.saved == [] and embedder.embedded == []
    assert "x" * 20 not in str(error.value) and "y" * 20 not in str(error.value)


async def test_unknown_or_inactive_images_and_bad_ids():
    service, repo, _ = _service(FakeRepo(active=False))
    with pytest.raises(UnknownImageError):
        await service.save(IMAGE, _request())
    with pytest.raises(UnknownImageError):
        await service.get(IMAGE)
    with pytest.raises(ProfileValidationError):
        await service.get("not-a-uuid")


async def test_get_of_an_unprofiled_meme_is_an_empty_profile():
    service, _, _ = _service()
    response = await service.get(IMAGE)
    assert response["exists"] is False and response["values"] == EMPTY_VALUES and response["vector"] == "none"
    assert response["currentTaxonomyVersion"] == TAX.version


async def test_needs_review_only_for_a_human_verified_profile_of_an_older_taxonomy():
    service, _, _ = _service(FakeRepo(view=_view(human_verified=True, taxonomy_version=TAX.version - 1)))
    assert (await service.get(IMAGE))["needsReview"] is True
    service, _, _ = _service(FakeRepo(view=_view(human_verified=False, taxonomy_version=TAX.version - 1)))
    assert (await service.get(IMAGE))["needsReview"] is False


async def test_confirm_marks_verified_with_the_current_taxonomy_version():
    service, repo, _ = _service(FakeRepo(view=_view(taxonomy_version=TAX.version - 1)))
    await service.confirm(IMAGE)
    assert repo.confirmed == [TAX.version]


async def test_confirm_refuses_a_missing_profile_and_a_removed_vocabulary_key():
    service, _, _ = _service()
    with pytest.raises(ProfileConflictError):
        await service.confirm(IMAGE)
    stale = _view(values={**EMPTY_VALUES, "emotion": [{"key": "removed-key", "confidence": 0.9, "source": "model"}]})
    service, repo, _ = _service(FakeRepo(view=stale))
    with pytest.raises(ProfileValidationError):
        await service.confirm(IMAGE)
    assert repo.confirmed == []


async def test_reset_deletes_and_is_idempotent_for_a_missing_profile():
    service, repo, _ = _service()
    await service.reset(IMAGE)
    assert len(repo.reset_calls) == 1


def test_the_use_when_limit_matches_the_extractor():
    from ai.meme_profile_extractor import MAX_USE_WHEN_CHARS as EXTRACTOR_LIMIT
    assert MAX_USE_WHEN_CHARS == EXTRACTOR_LIMIT
```

(Real taxonomy keys verified 2026-10-09: emotion `joy, amusement, pride, relief` (max 3 values), topic `work`; the 4-value case exceeds the emotion limit.)

- [ ] **Step 5: Implement the service** `Backend/app/services/meme_profile_service.py` (Task 3 adds the schema; the service returns plain dicts, which the router validates with the generated model: this keeps Task 2 independent of the generated types):

```python
"""Curator operations on meme profiles (P5c): validation, human save with an inline vector, confirm, reset."""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from repository.meme_profiles import AXES, ProfileData, ProfileMeta
from taxonomy import load as load_taxonomy
from taxonomy.validate import validate_profile

logger = logging.getLogger(__name__)

HUMAN = "human"
MAX_USE_WHEN_CHARS = 300   # equals ai.meme_profile_extractor.MAX_USE_WHEN_CHARS; a test pins it


class ProfileValidationError(ValueError):
    """Not acceptable. Messages are fixed or name an axis or taxonomy key, never free text."""


class UnknownImageError(LookupError):
    """The image does not exist or is not active."""


class ProfileConflictError(Exception):
    """The operation does not apply to the profile's current state."""


@dataclass(frozen=True)
class _Vector:
    values: list[float]
    digest: str


def _image_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ProfileValidationError("imageId is not a valid id") from None


class MemeProfileService:
    def __init__(self, repository, embedder, *, embedding_model: str, embed_timeout: float, taxonomy=None):
        self.repo = repository
        self.embedder = embedder
        self.model = embedding_model
        self.timeout = embed_timeout
        self.tax = taxonomy or load_taxonomy()

    async def get(self, image_id: str) -> dict:
        image = await self._active(image_id)
        return await self._response(image, await self.repo.get_view(image))

    async def save(self, image_id: str, request) -> dict:
        image = await self._active(image_id)
        data = self._validated(request)
        old = await self.repo.get_view(image)
        vector = None
        if data.situation_text and (old is None or old.situation_text != data.situation_text):
            # Computed BEFORE the write: no lock is held during the Ollama call, and a failure only means "pending".
            vector = await self._embed(image, data.situation_text)
        await self.repo.save_human_profile(
            image, data, ProfileMeta(self.tax.version, HUMAN, HUMAN, HUMAN, True))
        if vector is not None:
            await self.repo.save_situation_embedding(image, vector.values, self.model, vector.digest)
        return await self._response(image, await self.repo.get_view(image))

    async def confirm(self, image_id: str) -> dict:
        image = await self._active(image_id)
        view = await self.repo.get_view(image)
        if view is None:
            raise ProfileConflictError("this meme has no profile to confirm")
        for axis, items in view.values.items():
            if any(not self.tax.has_key(axis, item["key"]) for item in items):
                raise ProfileValidationError(
                    "the profile holds a value the current vocabulary no longer has; edit and save it instead")
        await self.repo.confirm(image, self.tax.version)
        return await self._response(image, await self.repo.get_view(image))

    async def reset(self, image_id: str) -> None:
        image = await self._active(image_id)
        await self.repo.reset(image)

    async def _active(self, image_id: str) -> uuid.UUID:
        image = _image_id(image_id)
        if not await self.repo.image_is_active(image):
            raise UnknownImageError()
        return image

    def _validated(self, request) -> ProfileData:
        profile = {"situation": request.situation}
        for axis in AXES:
            profile[axis] = [{"key": key, "confidence": 1.0, "source": HUMAN} for key in getattr(request.values, axis)]
        result = validate_profile(profile, self.tax)
        errors = list(result.errors)
        use_when = (request.useWhen or "").strip() or None
        if use_when and len(use_when) > MAX_USE_WHEN_CHARS:
            errors.append(f"useWhen: longer than {MAX_USE_WHEN_CHARS} characters")
        if errors:
            raise ProfileValidationError("the profile is not valid: " + "; ".join(errors))
        return ProfileData.from_validated(result.profile, use_when)

    async def _embed(self, image: uuid.UUID, text: str) -> "_Vector | None":
        deadline = time.monotonic() + self.timeout
        try:
            digest = await asyncio.to_thread(self.embedder.model_digest, self.model, max(deadline - time.monotonic(), 0.0))
            vectors = await asyncio.to_thread(self.embedder.embed, self.model, [text], deadline - time.monotonic())
            return _Vector(list(vectors[0]), digest)
        except Exception as error:   # any failure degrades to "pending"; never log the text
            logger.warning("profile vector not refreshed for %s: %s", image, type(error).__name__)
            return None

    async def _response(self, image: uuid.UUID, view) -> dict:
        base = {"imageId": str(image), "currentTaxonomyVersion": self.tax.version}
        if view is None:
            return {**base, "exists": False, "humanVerified": False, "needsReview": False, "taxonomyVersion": None,
                    "situation": None, "situationConfidence": None, "useWhen": None,
                    "values": {axis: [] for axis in AXES}, "vector": "none", "extractedAt": None, "updatedAt": None}
        return {**base, "exists": True, "humanVerified": view.human_verified,
                "needsReview": view.human_verified and view.taxonomy_version != self.tax.version,
                "taxonomyVersion": view.taxonomy_version, "situation": view.situation_text,
                "situationConfidence": view.situation_confidence, "useWhen": view.use_when, "values": view.values,
                "vector": await self.repo.vector_status(image, self.model),
                "extractedAt": view.extracted_at, "updatedAt": view.updated_at}
```

- [ ] **Step 6: Run the tests and commit.** `cd Backend && /h/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/test_meme_profile_service.py -q`, the whole Backend root, `pytest tests/retrieval -q`, the whole `tests/integration` root (settings change), `pytest tests/taxonomy -q`.

```bash
git add retrieval environments/settings.yaml Backend/app/services/meme_profile_service.py Backend/tests/test_meme_profile_service.py tests/retrieval
git commit -m "feat(profiles): add the model digest call, the profile service with inline vector refresh and the editor setting"
```

---

### Task 3: Routers, schemas, regenerated types and docs

**Files:**
- Create: `shared/schemas/curatedprofile.schema.json`, `curatedprofilesaverequest.schema.json`, `profilecoverage.schema.json`, `profilecoverageimages.schema.json`, `Backend/app/services/profile_coverage_service.py`, `Backend/app/api/meme_profiles.py`, `Backend/app/api/admin_profiles.py`, `Backend/tests/test_meme_profiles_endpoint.py`, `Backend/tests/test_profile_coverage_service.py`, `Backend/tests/test_admin_profiles_endpoint.py`
- Modify: `shared/schemas/all.schema.json`, `Backend/app/main.py`, `backend_api.md`; regenerate the three type trees.

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: generated `curatedprofile.Schema` (fields `imageId, exists, humanVerified, needsReview, taxonomyVersion, currentTaxonomyVersion, situation, situationConfidence, useWhen, values{emotion,stance,topic,tone: [ProfileValue]}, vector, extractedAt, updatedAt`), `curatedprofilesaverequest.Schema` (`situation, useWhen, values{emotion,stance,topic,tone: [str]}`), `profilecoverage.Schema`, `profilecoverageimages.Schema`; TypeScript `CuratedProfile`, `CuratedProfileSaveRequest`, `ProfileCoverage`, `ProfileCoverageImages`, `ProfileCoverageImage`; HTTP: `GET/PUT /api/meme-from-text/profiles/{imageId}`, `POST .../confirm`, `DELETE ...` (204), `GET /api/admin/profile-coverage`, `GET /api/admin/profile-coverage/images?category=&cursor=&limit=`; service `ProfileCoverageService(repository, runs, *, taxonomy_version, embedding_model)` with `async coverage() -> dict`, `async images(category, cursor, limit) -> dict`.

- [ ] **Step 1: Schemas.** Create the four files (same style as `ratingrequest.schema.json`; `$id` equals the file name, `title` as listed, every object `"additionalProperties": false`):

`curatedprofile.schema.json` (`title: "CuratedProfile"`): properties `imageId` string; `exists`, `humanVerified`, `needsReview` boolean; `taxonomyVersion` `["integer","null"]`; `currentTaxonomyVersion` integer; `situation`, `useWhen`, `extractedAt`, `updatedAt` `["string","null"]`; `situationConfidence` `["number","null"]`; `values` object (additionalProperties false) with required `emotion|stance|topic|tone`, each an array of `{"$ref": "./profilevalue.schema.json"}`; `vector` string enum `fresh|pending|none`; all properties required.

`curatedprofilesaverequest.schema.json` (`title: "CuratedProfileSaveRequest"`): `situation` `["string","null"]` `maxLength` 160, `useWhen` `["string","null"]` `maxLength` 300, `values` object with required arrays `emotion|stance|topic|tone` of strings (`maxLength` 48, `maxItems` 10 each); all required.

`profilecoverage.schema.json` (`title: "ProfileCoverage"`): integers `active, profiled, withoutProfile, humanVerified, needsReview, staleTaxonomy, failed, pendingVector, currentTaxonomyVersion`; `lastRuns` object with required `profiles` and `embeddings`, each `["object","null"]` of `{runId: string, status: string, startedAt: string, completedAt: ["string","null"], stats: ["object","null"]}` (additionalProperties false on the run object; `stats` free-form); all required.

`profilecoverageimages.schema.json` (`title: "ProfileCoverageImages"`): `items` array of objects `{id: string, imageUrl: string, humanVerified: boolean}` (all required, additionalProperties false; define the item inline so no extra file is needed, title it via `$defs`/`definitions` only if the generators need it: otherwise keep inline), `nextCursor` `["string","null"]`, `total` integer; all required.

Register in `all.schema.json` after the `"RatingsResponse"` line: `"CuratedProfile"`, `"CuratedProfileSaveRequest"`, `"ProfileCoverage"`, `"ProfileCoverageImages"`. Regenerate the three trees (Global Constraints). Expected real changes: four new `*.py` modules and `__init__.py`, `all.d.ts` (four interfaces, plus a named item type if the generator names it: check the generated name for the inline item and, if it is anonymous, define `ProfileCoverageImage` in `src/curator/types.ts` as `ProfileCoverageImages["items"][number]`), `Models.kt`. If the TypeScript generator rejects a `["string","null"]` union, use `oneOf` like the existing schemas do (look at how `queryprofile.schema.json` types `situation`).

- [ ] **Step 2: Failing tests.** `Backend/tests/test_meme_profiles_endpoint.py` (same pattern as `test_meme_text_ratings_endpoint.py`: a `FastAPI()` with the router and the app-level handler registered, dependency override `get_profile_service`; service stub with `AsyncMock`s returning plain dicts):

```python
IMAGE = str(uuid.uuid4())
PROFILE = {"imageId": IMAGE, "exists": True, "humanVerified": True, "needsReview": False, "taxonomyVersion": 2,
           "currentTaxonomyVersion": 2, "situation": "s", "situationConfidence": None, "useWhen": None,
           "values": {"emotion": [{"key": "joy", "confidence": 1.0, "source": "human"}], "stance": [], "topic": [], "tone": []},
           "vector": "fresh", "extractedAt": "2026-10-09T10:00:00", "updatedAt": "2026-10-09T10:00:00"}
BODY = {"situation": "s", "useWhen": None, "values": {"emotion": ["joy"], "stance": [], "topic": [], "tone": []}}
```

Tests: GET returns the profile; PUT passes the parsed body to `service.save(IMAGE, request)` and returns the profile; POST confirm returns it; DELETE returns 204 with an empty body; a malformed body (`situation` of 161 chars, `values` missing an axis, `values.emotion` a string) is 422 and the 422 body never contains the text (`"SECRET-SITUATION"` in the situation of an invalid body); `ProfileValidationError` -> 422 with its fixed message, `UnknownImageError` -> 404 `"image not found"`, `ProfileConflictError` -> 409 with its message; an unexpected `RuntimeError("boom SECRET")` -> generic 500 `"profile request failed"` with neither body nor `caplog` (DEBUG) containing `SECRET`, and a log record naming `RuntimeError`.

`Backend/tests/test_profile_coverage_service.py`: a `FakeRepo` with `live_counts` and `list_category` stubs and a `FakeRuns.get_most_recent_run(kind)` returning `SimpleNamespace(run_id=..., status="completed", created_at=datetime, completed_at=datetime|None, stats={...})` or `None`; tests: counts are mapped to camelCase keys with `currentTaxonomyVersion`; `lastRuns` has `profiles` from kind `build_meme_profiles` and `embeddings` from `build_meme_profile_embeddings` (`None` when no run), with ISO timestamps and the stats dict untouched; `images` returns `limit` items plus `nextCursor` = last returned id when the repository returned one more row, `None` otherwise (call the repository with `limit + 1`), `imageUrl == f"/api/images/{id}"`, a bad cursor raises `ProfileValidationError`, `total` is passed through.

`Backend/tests/test_admin_profiles_endpoint.py`: router with an overridden `get_profile_coverage_service`: the counts endpoint returns the dict; the images endpoint passes `category`, `cursor`, `limit` through; an unknown category and `limit=0`/`101` are 422; `ProfileValidationError` (bad cursor) -> 422.

- [ ] **Step 3: Implement.** `Backend/app/services/profile_coverage_service.py`:

```python
"""Profile coverage for the admin view (P5c): live counts, the last runs, category pages."""
import uuid

from Backend.app.services.meme_profile_service import ProfileValidationError

RUN_KINDS = {"profiles": "build_meme_profiles", "embeddings": "build_meme_profile_embeddings"}


def _run(run) -> dict | None:
    if run is None:
        return None
    return {"runId": str(run.run_id), "status": str(run.status), "startedAt": run.created_at.isoformat(),
            "completedAt": run.completed_at.isoformat() if run.completed_at else None, "stats": run.stats}


class ProfileCoverageService:
    def __init__(self, repository, runs, *, taxonomy_version: int, embedding_model: str):
        self.repo, self.runs = repository, runs
        self.taxonomy_version, self.model = taxonomy_version, embedding_model

    async def coverage(self) -> dict:
        counts = await self.repo.live_counts(self.taxonomy_version, self.model)
        return {
            "active": counts["active"], "profiled": counts["profiled"], "withoutProfile": counts["without_profile"],
            "humanVerified": counts["human_verified"], "needsReview": counts["needs_review"],
            "staleTaxonomy": counts["stale_taxonomy"], "failed": counts["failed"],
            "pendingVector": counts["pending_vector"], "currentTaxonomyVersion": self.taxonomy_version,
            "lastRuns": {name: _run(await self.runs.get_most_recent_run(kind)) for name, kind in RUN_KINDS.items()},
        }

    async def images(self, category: str, cursor: str | None, limit: int) -> dict:
        after = None
        if cursor:
            try:
                after = uuid.UUID(cursor)
            except ValueError:
                raise ProfileValidationError("cursor is not valid") from None
        rows, total = await self.repo.list_category(category, self.taxonomy_version, self.model, after, limit + 1)
        more = len(rows) > limit
        items = [{"id": str(image_id), "imageUrl": f"/api/images/{image_id}", "humanVerified": human}
                 for image_id, human in rows[:limit]]
        return {"items": items, "nextCursor": items[-1]["id"] if more and items else None, "total": total}
```

`Backend/app/api/meme_profiles.py` (router prefix `/meme-from-text`; the same `_fail` pattern as `meme_text_ratings.py`: fixed details, log only the type name; map `ProfileValidationError` 422, `UnknownImageError` 404 `"image not found"`, `ProfileConflictError` 409, other 500 `"profile request failed"`; dependency `get_profile_service(db)` builds `MemeProfileService(MemeProfileRepository(db), OllamaEmbedClient(resolve_ollama_url(str(settings.get("parse.ollama_url"))), PROFILE_EMBEDDING_DIM), embedding_model=str(settings.get("taxonomy.embedding_model")), embed_timeout=float(settings.get("profile_editor.embed_timeout_seconds", 8.0)))`; routes `GET /profiles/{image_id}`, `PUT /profiles/{image_id}` (body `CuratedProfileSaveRequest`), `POST /profiles/{image_id}/confirm`, `DELETE /profiles/{image_id}` (204 `Response`), each wrapping the service call in `try/except Exception as error: raise _fail(error) from None`, `response_model=CuratedProfile` with `CuratedProfile.model_validate(await service.get(...))` or let FastAPI validate the returned dict). `Backend/app/api/admin_profiles.py` (router prefix `/admin/profile-coverage`, `GET ""` and `GET "/images"`; `category: Literal[...]` via `Query`, `limit: int = Query(48, ge=1, le=100)`, `cursor: str | None = None`; dependency builds `ProfileCoverageService(MemeProfileRepository(db), BatchRunRepository(db), taxonomy_version=load().version, embedding_model=...)`). In `Backend/app/main.py` import both routers and `app.include_router(..., prefix="/api")` right after the ratings router (preserve line endings).

- [ ] **Step 4: Docs.** `backend_api.md`: a section "Meme profile curation" next to the P5b ratings section (same heading level and bullet style) documenting the four profile endpoints (request fields and limits, response fields, `vector` meaning, status codes 200/204/404/409/422/500, that edits take effect for the axis path at once and for the situation vector inline or after the next embeddings run, that Confirm refuses a profile with a removed vocabulary key, that Reset also clears the failure record, that the API is unauthenticated and last write wins) and the two admin coverage endpoints (counts, categories, last-run stats as of the run, cursor paging).

- [ ] **Step 5: Run and commit.** Whole Backend root, `pytest tests/retrieval -q`, `pytest tests/evaluation -q`, `cd Frontend/memes-frontend && pnpm exec tsc -b; echo "exit $?"`, then:

```bash
git add shared/schemas Backend/app Backend/tests backend_api.md Frontend/memes-frontend/src/types/generated AndroidClient
git commit -m "feat(profiles): add the curator and coverage endpoints, shared schemas and docs"
```

---

### Task 4: Frontend API layer

**Files:**
- Create: `Frontend/memes-frontend/src/curator/types.ts`, `src/curator/fixtures.ts`, `src/api/http/HttpMemesApi.curator.test.ts`
- Modify: `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/api/errors.ts`, `src/test/mockApi.ts`

**Interfaces:**
- Consumes: Task 3's generated types.
- Produces: `src/curator/types.ts`: re-exports `CuratedProfile, CuratedProfileSaveRequest, ProfileCoverage, ProfileCoverageImages` and `ProfileCoverageImage`, `type Category = "without_profile" | "needs_review" | "stale_taxonomy" | "failed" | "human_verified" | "pending_vector"`, `CATEGORY_CHOICES: readonly { key: Category; label: string; count: (coverage: ProfileCoverage) => number }[]` (labels: "Without profile", "Needs review", "Stale vocabulary", "Failed", "Human-verified", "Search vector pending"); `MemesApi` methods `getCuratedProfile(imageId)`, `saveCuratedProfile(imageId, request)`, `confirmCuratedProfile(imageId)`, `resetCuratedProfile(imageId): Promise<void>`, `getProfileCoverage()`, `getProfileCoverageImages(category, cursor?, limit?)`; `CuratorRequestError(status)` (fixed message `Curator request failed (HTTP <status>)`); fixtures `makeCuratedProfile(overrides)` (exists, human-verified false, taxonomy 2, situation "Manager scheduled a late meeting.", emotion annoyance model 0.9, stance sarcasm 0.6, topic work 0.9, vector "fresh"), `makeCoverage(overrides)`, `makeCoverageImages(ids, nextCursor?)`; `makeMockApi` defaults for all six methods.

- [ ] **Step 1: Failing client tests** (`src/api/http/HttpMemesApi.curator.test.ts`, same `stubFetch` helper style as `HttpMemesApi.ratings.test.ts`): GET profile URL `http://backend/api/meme-from-text/profiles/img-1`; PUT sends JSON and returns the body; POST confirm URL; DELETE reset URL and returns undefined on 204; `getProfileCoverage` URL `/api/admin/profile-coverage`; images URL with `category`, optional `cursor`, `limit` in the query (use `URLSearchParams`); each method maps a non-OK response to `CuratorRequestError` with the status and a message that contains neither the situation nor any body text (`it.each` over the six calls).

- [ ] **Step 2: Implement** the types file, error class, interface methods, HTTP methods (URL-encode ids with `encodeURIComponent`), fixtures and mock defaults, following `HttpMemesApi`'s existing `fetch` conventions (copy the ratings methods). `getCuratedProfile` 404 is also a `CuratorRequestError` (the panel decides what to show).

- [ ] **Step 3: Gates and commit.** `pnpm exec vitest run src/api` then the three gates.

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(frontend): add the curator API methods, error type, types and fixtures"
```

---

### Task 5: Curator panel on the meme page

**Files:**
- Create: `src/curator/draft.ts`, `src/curator/draft.test.ts`, `src/curator/useUnsavedGuard.ts`, `src/curator/useProfileEditor.ts`, `src/curator/useProfileEditor.test.ts`, `src/components/curator/ProfileEditor.tsx`, `src/components/curator/ProfileEditor.test.tsx`
- Modify: `src/pages/MemePage.tsx`, `src/pages/MemePage.test.tsx`

**Interfaces:**
- Consumes: Task 4; P5's `ChipGroup`, `useTaxonomy`, `labelFor/optionsFor/maxValuesFor/axisSpec`, `AXES`, `AXIS_LABELS`, `SITUATION_LIMIT`, `TEST_TAXONOMY` (keys: emotion `annoyance|joy|resignation|pride`, stance `sarcasm|congratulate`, topic `work|food`, tone `sarcastic|wholesome`).
- Produces: `draft.ts`: `type Draft = { situation: string; useWhen: string; values: Record<AxisName, string[]> }`, `emptyDraft()`, `toDraft(profile: CuratedProfile | null): Draft` (empty for `null` or `exists: false`), `sameDraft(a, b)` (texts compared trimmed, key lists as sets), `toSaveRequest(draft): CuratedProfileSaveRequest` (blank texts become `null`, trimmed), `addKey(draft, axis, key)`, `removeKey(draft, axis, key)` (no duplicates; add is a no-op past the axis maximum is NOT enforced here: the picker limits it); `useUnsavedGuard(active: boolean)` returning react-router's `Blocker` (uses `useBlocker(active)` and a `beforeunload` listener while active); `useProfileEditor(memesApi, imageId)` returning `{ status: "loading" | "ready" | "error", profile: CuratedProfile | null, draft: Draft, dirty: boolean, busy: "save" | "confirm" | "reset" | null, event: EditorEvent | null, setSituation(text), setUseWhen(text), add(axis, key), remove(axis, key), discard(), save(), confirm(), reset() }` with `type EditorEvent = { kind: "saved"; vector: "fresh" | "pending" | "none" } | { kind: "confirmed" } | { kind: "reset" } | { kind: "failed"; action: "save" | "confirm" | "reset" }`; `ProfileEditor({ memesApi, imageId })`.

- [ ] **Step 1: Failing tests.**

`draft.test.ts`: `toDraft` of `makeCuratedProfile()` has the keys per axis and the texts; `toDraft(null)` and a `{exists:false}` profile are empty; `sameDraft` ignores surrounding whitespace and key order and sees a changed key; `toSaveRequest` trims, nulls blank texts and keeps all four axes; `addKey` does not duplicate, `removeKey` of a missing key is a no-op, both return new objects (inputs untouched).

`useProfileEditor.test.ts` (`renderHook`, `makeMockApi`; helper `deferred<T>()` as in `useRatings.test.ts`): loads the profile (`status` loading then ready, draft equals the profile); an edit makes `dirty` true and editing back makes it false; `save` sends `toSaveRequest(draft)` for the image id, replaces the profile with the response, clears the edits and reports `{kind:"saved", vector}` (pending vector passes through); a failed save keeps the edits, reports `{kind:"failed", action:"save"}` and `busy` returns to null; `confirm` calls `confirmCuratedProfile` and reports confirmed; `reset` calls `resetCuratedProfile`, then the profile becomes an empty one (`exists: false`, same `currentTaxonomyVersion`) and edits clear, and a failed reset leaves the profile; a second action while `busy` is ignored (one API call); `discard` drops the edits; a failed load gives `status: "error"`; an `imageId` change reloads and drops edits; loads under `StrictMode` (`wrapper: StrictMode`).

`ProfileEditor.test.tsx` (render inside a data router so `useBlocker` works: `createMemoryRouter([{ path: "/", element: <ProfileEditor .../> }, { path: "/other", element: <p>other</p> }])` + `RouterProvider`; navigate with `router.navigate("/other")` inside `act`):
- shows "Model profile" for a model profile, "Human-verified" for a verified one, and the "Needs review" banner text ("The vocabulary changed since this profile was verified") for `needsReview: true`; the chips show taxonomy labels ("Annoyance"), the situation and use-when text areas hold the values;
- editing the situation enables Save and shows "Unsaved changes"; Save calls `saveCuratedProfile("img-1", {...})` with the edited text and the same axis keys, then shows "Saved" in the always-mounted `role="status"` element (the same node before and after); a `vector: "pending"` response announces "Saved. The search vector is pending and will be refreshed by the next embeddings run." and shows it visibly;
- removing a chip (`Remove emotion: Annoyance`) and adding one through the picker enable Save; `Discard changes` restores the profile;
- "Confirm as correct" is shown for a model profile and for a needs-review profile, hidden for a verified fresh one and while there are unsaved edits; clicking it calls `confirmCuratedProfile` and announces "Confirmed as correct";
- "Reset to model" opens an inline confirm step (button names "Confirm reset" / "Cancel"), only "Confirm reset" calls `resetCuratedProfile`, then the panel shows the unprofiled state ("No profile yet") and announces "Reset. The next batch run re-extracts this profile."; for an unprofiled meme the button is hidden and the panel offers Save for a new profile (disabled while the draft is empty);
- a failed save shows `role="alert"` "Couldn't save the profile" and keeps the edits and focus on the Save button;
- with unsaved edits, navigating away is blocked and an inline bar appears ("You have unsaved changes." with "Leave without saving" and "Stay"); "Stay" keeps the page, "Leave without saving" completes the navigation; with no edits navigation is not blocked;
- buttons have `min-h-11`; the texts of the profile never appear in an `alert`.

`MemePage.test.tsx`: the curator panel is rendered below the details for the loaded meme (assert the "Profile" heading appears and `getCuratedProfile` was called with the meme id).

- [ ] **Step 2: Implement.** Pure helpers first, then the hook, with this state shape (derive, never set state in an effect; the load effect only calls setters from promise callbacks; ignore a result whose request id is no longer current):

```ts
type Loaded = { id: string; profile: CuratedProfile | null; failed: boolean }
const [loaded, setLoaded] = useState<Loaded | null>(null)
const [edits, setEdits] = useState<{ id: string; draft: Draft } | null>(null)
const [busy, setBusy] = useState<Busy | null>(null)
const [event, setEvent] = useState<{ id: string; event: EditorEvent } | null>(null)
const current = loaded !== null && loaded.id === imageId ? loaded : null
const profile = current?.profile ?? null
const base = toDraft(profile)
const draft = edits !== null && edits.id === imageId ? edits.draft : base
const dirty = edits !== null && edits.id === imageId && !sameDraft(edits.draft, base)
const status = current === null ? "loading" : current.failed ? "error" : "ready"
```

`save/confirm/reset` share one `run` helper: ignore when `busy !== null`; set busy; await the call; on success `setLoaded({ id: imageId, profile: next, failed: false })`, `setEdits(null)`, set the event; on failure set `{ kind: "failed", action }`; `finally` clear busy (only if the image id is still current). `reset` builds the empty profile locally from `profile.currentTaxonomyVersion` and `imageId` after a successful `resetCuratedProfile` call (no second request). `ProfileEditor` uses `useTaxonomy(memesApi, profile?.currentTaxonomyVersion ?? null)` and, per axis, `ChipGroup` exactly as `ReadingPanel` does (`values` mapped from the draft keys with the saved item's confidence and source, else `{confidence: 1, source: "human"}`; `labelOf/options/maxValues/pickerDisabled` from the taxonomy helpers), a labelled situation text area (`maxLength` from `axisSpec(taxonomy, "situation")?.maxChars ?? SITUATION_LIMIT`) and a use-when text area (`maxLength` 300), an always-mounted `<p role="status" className="sr-only">` carrying the announcement, a visible secondary line for a pending vector, `role="alert"` for failures, `min-h-11` on every button, and the unsaved-changes bar from `useUnsavedGuard(dirty)`. Focus: after Save, Confirm, Reset and Discard, focus stays on (or returns to) the button that was used if it is still mounted, otherwise on the panel heading (`tabIndex={-1}`); use a ref that is set in the handler and consumed in a post-state effect (no state set in the effect), as `ForgetTextButton` does. Mount `<ProfileEditor memesApi={memesApi} imageId={meme.id} />` in `MemePage` below `<MemeDetails .../>`.

- [ ] **Step 3: Gates and commit.** `pnpm exec vitest run src/curator src/components/curator src/pages/MemePage.test.tsx`, then the four gates (`tsc -b`, `eslint src/ --max-warnings 0`, whole `vitest run`, `vite build`).

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(frontend): add the meme-profile curator panel on the meme page"
```

---

### Task 6: Coverage page, route and navigation

**Files:**
- Create: `src/curator/useProfileCoverage.ts`, `src/curator/useProfileCoverage.test.ts`, `src/pages/ProfileCoveragePage.tsx`, `src/pages/ProfileCoveragePage.test.tsx`
- Modify: `src/app/router.tsx`, `src/app/AppLayout.tsx` (and its test if one asserts the nav links; find it with `ls src/app`)

**Interfaces:**
- Consumes: Task 4 (`getProfileCoverage`, `getProfileCoverageImages`, `Category`, `CATEGORY_CHOICES`, `makeCoverage`, `makeCoverageImages`).
- Produces: `useProfileCoverage(memesApi)` returning `{ coverage: ProfileCoverage | null, failed: boolean, category: Category, select(category), items: ProfileCoverageImage[], total: number, loading: boolean, listFailed: boolean, hasMore: boolean, loadMore() }`; `ProfileCoveragePage({ memesApi })`; route `/admin/profiles`; nav link "Profiles".

- [ ] **Step 1: Failing tests.**

`useProfileCoverage.test.ts`: loads the coverage once (also under `StrictMode`); the first category is `without_profile` and its first page is requested with that category; `select("failed")` requests page one of the new category and drops the old items (no mixing); `loadMore` requests the next page with the previous `nextCursor` and appends; `hasMore` follows `nextCursor`; a result that arrives for a category that is no longer selected is ignored (deferred promises); a failed coverage call sets `failed`, a failed list call sets `listFailed` and keeps earlier items; `loadMore` is ignored while loading.

`ProfileCoveragePage.test.tsx`: renders a heading "Profile coverage" and one card per category as a toggle button with its count (`aria-pressed` true only for the selected one); clicking a card selects it, announces "<label>: <n> memes" in an always-mounted `role="status"`, and shows its thumbnails as links to `/memes/<id>` (inside a `MemoryRouter`); a "Show more" button appears only when more pages exist and appends; an empty category shows "Nothing in this category"; the "As of the last run" block shows the profile job's `stale`, `needs_review`, `failed` from `lastRuns.profiles.stats` with its completed time and says "No run yet" for a `null` run; the live numbers (active, profiled) are shown; a failed coverage load shows `role="alert"` "Couldn't load the coverage" with a Try again button that reloads; human-verified thumbnails carry a visible "Verified" mark; buttons have `min-h-11`.

- [ ] **Step 2: Implement.** The hook keeps `{ category, page: { category, items, next, total } | null, loading }` and derives `items` as `page?.category === category ? page.items : []`; fetches happen from the selection handler and from one mount effect (cancelled flag, results guarded by the category they were requested for), never with a synchronous setState in an effect. The page lays out the cards as a responsive grid of buttons (use `CATEGORY_CHOICES` for labels and counts), the last-run block, and a thumbnail grid whose `<img>` uses `memesApi.getImageUrlById(id)` with an `alt` of `Meme <short id>` and `loading="lazy"`; link targets `/memes/${id}`. Router: add `{ path: "/admin/profiles", element: <ProfileCoveragePage memesApi={memesApi} /> }` next to the `/admin` route. AppLayout: add a "Profiles" `NavLink` to `/admin/profiles` directly after "Admin" with the same class function, and give the "Admin" `NavLink` the `end` prop so it is not active on the profiles page.

- [ ] **Step 3: Gates and commit.** `pnpm exec vitest run src/curator src/pages src/app`, then the four gates.

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(frontend): add the profile coverage page, route and navigation"
```

---

### Task 7: Live check, docs and review report (controller steps)

Controller only (needs a seeded database, Ollama and a browser). **Do not delegate any of it.**

- [ ] **Step 1: Seed and start a minimal stack on the test database** (never a real environment, never 8081-8083 or 5173-5175): a seed script like P5b's (`p5b_seed.py`, prefix `p5c-live-`) with ~14 synthetic images carrying model profiles (varied axes), one human-verified profile on an older taxonomy version, one failure row for the profile pipeline, one image without a profile, vectors for some; a minimal FastAPI app registering the images, taxonomy, profiles and admin-profiles routers plus the 422 handler, and the Vite dev server on a free port.
- [ ] **Step 2: API check:** GET of a profiled and an unprofiled meme; PUT a new profile (check `vector: "fresh"`, the stored `meme_profile_situation_embeddings` row has the same `model_name` and `model_digest` that `build_meme_profile_embeddings` would store: compare against `ai.ollama_embed.OllamaEmbedder("bge-large").model_digest()`); PUT with Ollama pointed at a dead port (`OLLAMA_HOST=127.0.0.1:9`) gives `pending` and a saved profile; Confirm; Reset (profile and failure row gone); invalid bodies return 422 without the text; coverage counts equal the category totals.
- [ ] **Step 3: Browser check:** open the meme page of a model profile, edit chips and text, save (announcement, vector notice), confirm another, reset another, the unsaved guard on navigation; open `/admin/profiles`, switch categories, page, follow a thumbnail to its meme page, edit there, come back and see the counts change.
- [ ] **Step 4: Race check** (test database only; the batch job is never run against a real database for this): a script that saves a human profile through the API, then calls `MemeProfileRepository.save_profile` the way the batch does (expected `False`, the human profile is intact), then resets through the API and calls `save_profile` again (expected `True`, the model profile is written).
- [ ] **Step 5: Documentation:** P5c spec addendum (what was built, live check, deviations from the list above, rollout note: no migration, restart the backends), tracker row P5c `done`, `CLAUDE.md` entry (a curator paragraph next to the ratings entry: endpoints, the human-verified staleness rule, the inline vector behaviour, `/admin/profiles`), a one-line pointer in the P2 spec's "needs review" description to P5c decision 2, `docs/superpowers/reviews/2026-10-09-p5c-curator-tools-review.md`.
- [ ] **Step 6: Final verification and cleanup:** every gate (frontend four, Backend root, `tests/retrieval`, `tests/evaluation`, `tests/taxonomy`, `batch/tests`, the whole `tests/integration`), generated types without diff; remove the seeded rows, stop the servers, close the browser tab; commit; wait for the user's word to merge. Tell the user to restart the backends (no migration).

---

## Self-review (plan against spec)

| Spec item | Task |
|---|---|
| Decision 1: save, confirm, reset semantics, validation limits | 1 (repository), 2 (service) |
| Decision 2: human-verified staleness only on a taxonomy bump | 1 |
| Decision 3: inline vector refresh, `vector` status | 2 (deviation 1: computed before the write) |
| Decision 4: endpoints, schemas, types, `backend_api.md` | 3 |
| Decision 5: curator panel and `/admin/profiles` | 5, 6 |
| Decision 6: race and consistency rules | 1 (repository tests), 7 (race check) |
| Acceptance 1 to 5 | tests in 1-6, gates in every task |
| Acceptance 6 (addendum, tracker, `CLAUDE.md`, review report) | 7 |

Type and name consistency checks: the repository's `CATEGORIES`, `live_counts` keys and `list_category` signature (Task 1) are the ones the coverage service (Task 3) calls; `MemeProfileService` returns plain dicts shaped like the `CuratedProfile` schema (Task 3), and the frontend `CuratedProfile` is generated from the same schema; the category string values are identical in Python, the schema enum, the query parameter and `Category` in `src/curator/types.ts`; `EditorEvent.vector` uses the schema's `fresh | pending | none`.
