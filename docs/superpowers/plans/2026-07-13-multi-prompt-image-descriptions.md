# Multi-prompt, configurable-model image descriptions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-model, single-prompt `batch/build_image_descriptions.py` with a version that runs a configurable list of prompts (each optionally on its own Ollama model) per image, storing one row per (image, prompt) pair, committing incrementally, and reporting progress/ETA the way the OCR batch does.

**Architecture:** `ImageDescription` (renamed from `OllamaDescription`) gains `prompt_key` + `model_used` columns and a `(image_id, prompt_key)` unique constraint, which is what makes "run only the missing pairs" possible on rerun. Prompts live in a per-environment tracked YAML file (`image_descriptions.prompts_file`), loaded by a small `ai/image_description_prompts.py` module that also resolves each prompt's model (per-prompt override, falling back to `image_descriptions.model`) — this mirrors the existing `batch/trends/resolution.py` pattern exactly. A new `ImageDescriptionEmbedding` table is created (schema only, unpopulated) for future text-embedding work.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, Alembic, Dynaconf, `ollama` Python client, PyYAML, pytest / pytest-asyncio.

**Full design reference:** `docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md` — read it if anything in a task feels underspecified.

## Global Constraints

- Target Python is 3.11 (`.venv311`) — run all commands with that venv active.
- Repositories must never call `session.commit()` except via the batch script's own committer wrapper — `get_async_db` handles commit/rollback for the Backend; batch scripts own their own session lifecycle.
- ORM models live only in `Storage/models.py`.
- `backend_api.md` must stay in sync with routers — this plan touches no routers, so no update needed there.
- Windows dev requires `WATCHFILES_FORCE_POLLING=1` for uvicorn `--reload` — not relevant to this plan (no Backend endpoint changes).
- No automated test gate exists for batch scripts or the Backend as a whole; where a task has no existing test precedent (e.g. `BatchCommitter`-style wrapper classes), match that precedent instead of inventing new test scaffolding.

---

### Task 1: Tracked config — prompts file + settings keys

**Files:**
- Modify: `environments/settings.yaml`
- Modify: `environments/settings.general.yaml`
- Create: `batch/data/image-description-prompts.general.yaml`
- Modify: `batch/tests/test_env_loading.py`
- Modify: `Backend/tests/test_config_integration.py`

**Interfaces:**
- Produces: tracked settings keys `image_descriptions.model` (global default, all envs) and `image_descriptions.prompts_file` (general env only for now). Consumed via `settings.get("image_descriptions.model")` / `settings.get("image_descriptions.prompts_file")` in later tasks.

- [ ] **Step 1: Add the new keys to both config-integration test files' expectations (failing first)**

In `batch/tests/test_env_loading.py`, add to `_COMMON` (after `"RULES.FILE": None,`):

```python
    "IMAGE_DESCRIPTIONS.MODEL": "llava",
    "IMAGE_DESCRIPTIONS.PROMPTS_FILE": None,
```

Add to `_EXPECTED["general"]` (after `"GENERAL.FRONTEND_ORIGIN": "http://localhost:5174",`):

```python
        "IMAGE_DESCRIPTIONS.MODEL": "qwen2.5vl:7b",
        "IMAGE_DESCRIPTIONS.PROMPTS_FILE": "data/image-description-prompts.general.yaml",
```

Make the identical two edits in `Backend/tests/test_config_integration.py` (same `_COMMON` / `_EXPECTED["general"]` shape, same key names and values).

- [ ] **Step 2: Run both test files to confirm they fail**

```bash
pytest batch/tests/test_env_loading.py Backend/tests/test_config_integration.py -v
```

Expected: FAIL — `KeyError` or assertion mismatch, since `IMAGE_DESCRIPTIONS.MODEL` / `IMAGE_DESCRIPTIONS.PROMPTS_FILE` don't resolve yet (`settings.get` returns `None` for both, which mismatches the `"llava"` / `"qwen2.5vl:7b"` expectations).

- [ ] **Step 3: Add the config keys**

In `environments/settings.yaml`, add after the `ollama:` block:

```yaml
image_descriptions:
  model: llava
```

In `environments/settings.general.yaml`, add after the `trends:` block:

```yaml
image_descriptions:
  model: qwen2.5vl:7b
  prompts_file: data/image-description-prompts.general.yaml
```

- [ ] **Step 4: Create the prompts file**

Create `batch/data/image-description-prompts.general.yaml`:

```yaml
- key: general_description
  prompt: "What is shown in this image?"
```

This preserves today's exact prompt text as the sole entry, so behavior is unchanged until a human adds more prompts to this file.

- [ ] **Step 5: Run the tests again to confirm they pass**

```bash
pytest batch/tests/test_env_loading.py Backend/tests/test_config_integration.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add environments/settings.yaml environments/settings.general.yaml \
  batch/data/image-description-prompts.general.yaml \
  batch/tests/test_env_loading.py Backend/tests/test_config_integration.py
git commit -m "feat: add image_descriptions config keys and general prompts file"
```

---

### Task 2: Schema — rename OllamaDescription, add prompt/model tracking, add embeddings table

**Files:**
- Modify: `Storage/models.py`
- Create: `Storage/alembic/versions/2026_07_13_rename_ollama_description_to_image_descriptions.py`
- Modify: `docs/schema.md`

**Interfaces:**
- Produces: `ImageDescription` ORM class (table `image_descriptions`, columns `id`, `image_id`, `prompt_key`, `model_used`, `text`, `created_at`, unique constraint `uq_image_description_image_prompt` on `(image_id, prompt_key)`). `ImageDescriptionEmbedding` ORM class (table `image_description_embeddings`, columns `id`, `image_description_id` (unique FK), `embedding` (`Vector(1024)`), `created_at`). New constant `TEXT_EMBEDDING_DIM = 1024`.
- Consumed by: Task 5 (`repository/image_descriptions.py`, `repository/images.py`), Task 6 (`Backend/app/repositories/diagnostics_repository.py`).

- [ ] **Step 1: Update `Storage/models.py`**

Add `UniqueConstraint` to the sqlalchemy import (line 4-8 currently reads):

```python
from sqlalchemy import (
    Column, String, Integer, Float, Text, ForeignKey,
    DateTime, JSON, func, Numeric, Index, Boolean,
    BigInteger
)
```

Change to:

```python
from sqlalchemy import (
    Column, String, Integer, Float, Text, ForeignKey,
    DateTime, JSON, func, Numeric, Index, Boolean,
    BigInteger, UniqueConstraint
)
```

Add `TEXT_EMBEDDING_DIM` next to the existing `EMBEDDING_DIM`:

```python
EMBEDDING_DIM = 512

TEXT_EMBEDDING_DIM = 1024
```

In the `Image` class, change:

```python
    descriptions = relationship("OllamaDescription", back_populates="image", cascade="all, delete-orphan")
```

to:

```python
    descriptions = relationship("ImageDescription", back_populates="image", cascade="all, delete-orphan")
```

Replace the entire `OllamaDescription` class:

```python
class OllamaDescription(Base):
    __tablename__ = "ollama_description"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    text = Column(Text, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="descriptions")
```

with:

```python
class ImageDescription(Base):
    __tablename__ = "image_descriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    prompt_key = Column(String, nullable=False)
    model_used = Column(String, nullable=False)
    text = Column(Text, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("image_id", "prompt_key", name="uq_image_description_image_prompt"),
    )

    image = relationship("Image", back_populates="descriptions")
    embedding = relationship(
        "ImageDescriptionEmbedding", uselist=False,
        back_populates="description", cascade="all, delete-orphan",
    )


class ImageDescriptionEmbedding(Base):
    __tablename__ = "image_description_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    image_description_id = Column(
        UUID(as_uuid=True), ForeignKey("image_descriptions.id", ondelete="CASCADE"),
        index=True, unique=True,
    )
    embedding = Column(Vector(TEXT_EMBEDDING_DIM), index=True)
    created_at = Column(DateTime, server_default=func.now())

    description = relationship("ImageDescription", back_populates="embedding")
```

- [ ] **Step 2: Create the Alembic migration**

Current head is `d4a1f7b2c9e6` (`d4a1f7b2c9e6_generalize_trend_sources.py`). Create `Storage/alembic/versions/2026_07_13_rename_ollama_description_to_image_descriptions.py`:

```python
"""rename ollama_description to image_descriptions, add prompt/model tracking

Revision ID: f1a2b3c4d5e6
Revises: d4a1f7b2c9e6
Create Date: 2026-07-13

"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = 'f1a2b3c4d5e6'
down_revision = 'd4a1f7b2c9e6'
branch_labels = None
depends_on = None

# Matches Storage.models.TEXT_EMBEDDING_DIM. Hardcoded rather than imported —
# migrations must stay valid even if the model constant changes later.
TEXT_EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.rename_table('ollama_description', 'image_descriptions')
    op.execute("ALTER INDEX ix_ollama_description_id RENAME TO ix_image_descriptions_id")
    op.execute("ALTER INDEX ix_ollama_description_image_id RENAME TO ix_image_descriptions_image_id")

    op.add_column('image_descriptions', sa.Column('prompt_key', sa.String(), nullable=True))
    op.add_column('image_descriptions', sa.Column('model_used', sa.String(), nullable=True))

    op.execute("UPDATE image_descriptions SET prompt_key = 'legacy' WHERE prompt_key IS NULL")
    op.execute("UPDATE image_descriptions SET model_used = 'llava' WHERE model_used IS NULL")

    op.alter_column('image_descriptions', 'prompt_key', nullable=False)
    op.alter_column('image_descriptions', 'model_used', nullable=False)

    op.create_unique_constraint(
        'uq_image_description_image_prompt', 'image_descriptions', ['image_id', 'prompt_key']
    )

    op.create_table(
        'image_description_embeddings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('image_description_id', sa.UUID(), nullable=True),
        sa.Column('embedding', Vector(TEXT_EMBEDDING_DIM), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_description_id'], ['image_descriptions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('image_description_id'),
    )
    op.create_index(
        op.f('ix_image_description_embeddings_image_description_id'),
        'image_description_embeddings', ['image_description_id'], unique=True,
    )
    op.create_index(
        op.f('ix_image_description_embeddings_embedding'),
        'image_description_embeddings', ['embedding'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_image_description_embeddings_embedding'), table_name='image_description_embeddings')
    op.drop_index(op.f('ix_image_description_embeddings_image_description_id'), table_name='image_description_embeddings')
    op.drop_table('image_description_embeddings')

    op.drop_constraint('uq_image_description_image_prompt', 'image_descriptions', type_='unique')
    op.drop_column('image_descriptions', 'model_used')
    op.drop_column('image_descriptions', 'prompt_key')

    op.execute("ALTER INDEX ix_image_descriptions_image_id RENAME TO ix_ollama_description_image_id")
    op.execute("ALTER INDEX ix_image_descriptions_id RENAME TO ix_ollama_description_id")
    op.rename_table('image_descriptions', 'ollama_description')
```

- [ ] **Step 3: Verify the migration is well-formed (offline render, no live DB needed)**

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" alembic upgrade head --sql | tail -60
cd ..
```

Expected: SQL output ending with the `ALTER TABLE image_descriptions ...`, `CREATE TABLE image_description_embeddings ...` statements, no Python traceback. Also confirm there's exactly one head:

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" alembic heads
cd ..
```

Expected: prints exactly one line, `f1a2b3c4d5e6 (head)`.

- [ ] **Step 4: Update `docs/schema.md`**

Replace the existing `### \`ollama_description\`` section (currently):

```markdown
### `ollama_description`
LLM-generated image descriptions. One row per image (re-run overwrites via batch clear).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_id` | UUID FK → images | |
| `text` | Text NOT NULL | Full description text |
| `created_at` | DateTime | |
```

with:

```markdown
### `image_descriptions`
LLM-generated image descriptions, one row per (image, prompt_key) pair. `prompt_key` corresponds to an entry in the environment's `image_descriptions.prompts_file` config; `model_used` records which model actually produced the text. Incremental batch runs fill only missing (image, prompt_key) pairs; `--reset` clears all rows for a full regeneration.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_id` | UUID FK → images | |
| `prompt_key` | String NOT NULL | Unique together with `image_id` |
| `model_used` | String NOT NULL | Model that generated this row's text |
| `text` | Text NOT NULL | Full description text |
| `created_at` | DateTime | |

### `image_description_embeddings`
Placeholder table for future text-embedding-based image linking — not yet populated by any batch job. One row per `image_descriptions` row.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `image_description_id` | UUID FK → image_descriptions, unique | |
| `embedding` | Vector(1024) | Dimension provisional — see `docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md` |
| `created_at` | DateTime | |
```

- [ ] **Step 5: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/2026_07_13_rename_ollama_description_to_image_descriptions.py docs/schema.md
git commit -m "feat: rename OllamaDescription to ImageDescription, add prompt/model tracking and embeddings table"
```

---

### Task 3: Prompt config loader

**Files:**
- Create: `ai/image_description_prompts.py`
- Test: `tests/ai/test_image_description_prompts.py`

**Interfaces:**
- Produces: `PromptConfig` dataclass (`key: str`, `prompt: str`, `model: str | None = None`), `load_prompts(path: str) -> list[PromptConfig]`, `resolve_model(prompt: PromptConfig, settings) -> str`.
- Consumed by: Task 7 (`batch/build_image_descriptions.py`).

- [ ] **Step 1: Write the failing test**

Create `tests/ai/test_image_description_prompts.py`:

```python
import pytest

from ai.image_description_prompts import PromptConfig, load_prompts, resolve_model


class _FakeSettings:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def test_load_prompts_parses_all_fields(tmp_path):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "- key: general_description\n"
        "  prompt: \"What is shown in this image?\"\n"
        "- key: humor_explanation\n"
        "  prompt: \"Explain the joke, if any.\"\n"
        "  model: llava\n"
    )

    prompts = load_prompts(str(path))

    assert prompts == [
        PromptConfig(key="general_description", prompt="What is shown in this image?", model=None),
        PromptConfig(key="humor_explanation", prompt="Explain the joke, if any.", model="llava"),
    ]


def test_load_prompts_raises_on_duplicate_key(tmp_path):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "- key: dup\n"
        "  prompt: \"first\"\n"
        "- key: dup\n"
        "  prompt: \"second\"\n"
    )

    with pytest.raises(ValueError, match="dup"):
        load_prompts(str(path))


def test_resolve_model_uses_prompt_override_when_present():
    prompt = PromptConfig(key="k", prompt="p", model="qwen2.5vl:7b")
    settings = _FakeSettings({"image_descriptions.model": "llava"})

    assert resolve_model(prompt, settings) == "qwen2.5vl:7b"


def test_resolve_model_falls_back_to_global_default():
    prompt = PromptConfig(key="k", prompt="p", model=None)
    settings = _FakeSettings({"image_descriptions.model": "llava"})

    assert resolve_model(prompt, settings) == "llava"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/ai/test_image_description_prompts.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ai.image_description_prompts'`.

- [ ] **Step 3: Implement**

Create `ai/image_description_prompts.py`:

```python
from dataclasses import dataclass

import yaml


@dataclass
class PromptConfig:
    key: str
    prompt: str
    model: str | None = None


def load_prompts(path: str) -> list[PromptConfig]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    prompts = [PromptConfig(**entry) for entry in raw]

    seen_keys = set()
    for prompt in prompts:
        if prompt.key in seen_keys:
            raise ValueError(f"Duplicate prompt key in {path}: {prompt.key!r}")
        seen_keys.add(prompt.key)

    return prompts


def resolve_model(prompt: PromptConfig, settings) -> str:
    return prompt.model or settings.get("image_descriptions.model")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/ai/test_image_description_prompts.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add ai/image_description_prompts.py tests/ai/test_image_description_prompts.py
git commit -m "feat: add prompt config loader and per-prompt model resolution"
```

---

### Task 4: Configurable prompt/model in OllamaImageDescriber

**Files:**
- Modify: `ai/ollama.py`
- Test: `tests/ai/test_ollama.py`

**Interfaces:**
- Produces: `OllamaImageDescriber.describe(path: str, prompt: str, model: str) -> str` (replaces the old zero-arg-besides-`path` signature).
- Consumed by: Task 7 (`batch/build_image_descriptions.py`).

- [ ] **Step 1: Write the failing test**

Add to `tests/ai/test_ollama.py` (it already has `import ollama` at the top):

```python
from ai.ollama import OllamaImageDescriber


def test_describe_passes_prompt_and_model_through(monkeypatch):
    def fake_chat(model, messages):
        assert model == "qwen2.5vl:7b"
        assert messages[0]["content"] == "Explain the joke."
        assert messages[0]["images"] == ["/path/to/image.jpg"]
        return {"message": {"content": "a description"}}

    monkeypatch.setattr(ollama, "chat", fake_chat)
    describer = OllamaImageDescriber()

    result = describer.describe("/path/to/image.jpg", "Explain the joke.", "qwen2.5vl:7b")

    assert result == "a description"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/ai/test_ollama.py -v -k describe
```

Expected: FAIL — `TypeError: describe() takes 2 positional arguments but 4 were given`.

- [ ] **Step 3: Implement**

In `ai/ollama.py`, replace:

```python
class OllamaImageDescriber:

    def describe(self, path: str) -> str:
        response = ollama.chat(
            model='llava',
            messages=[{
                'role': 'user',
                'content': 'What is shown in this image?',
                'images': [path]
            }]
        )
        return response['message']['content']
```

with:

```python
class OllamaImageDescriber:

    def describe(self, path: str, prompt: str, model: str) -> str:
        response = ollama.chat(
            model=model,
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [path]
            }]
        )
        return response['message']['content']
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/ai/test_ollama.py -v
```

Expected: PASS (all tests in the file, including the pre-existing `OllamaConceptNamer` ones).

- [ ] **Step 5: Commit**

```bash
git add ai/ollama.py tests/ai/test_ollama.py
git commit -m "feat: make OllamaImageDescriber prompt and model configurable per call"
```

---

### Task 5: Image descriptions repository + ImagesRepository rename

**Files:**
- Create (via `git mv` from `repository/ollama_descriptions.py`): `repository/image_descriptions.py`
- Modify: `repository/images.py`
- Test: `tests/integration/test_image_descriptions_repository.py`

**Interfaces:**
- Produces: `ImageDescriptionsRepository` with `delete_all()`, `save(image_id, prompt_key: str, model_used: str, text: str)`, `get_all_texts() -> list[str]`, `get_image_ids_with_prompt(prompt_key: str) -> set[uuid.UUID]`. `ImagesRepository.get_images_and_descriptions()` / `get_images_and_descriptions_without_tags(source)` (renamed from the `_ollama_` versions); `get_all_images_without_description` removed.
- Consumed by: Task 6 (`batch/build_tags_from_descriptions.py`, `batch/build_bow.py`), Task 7 (`batch/build_image_descriptions.py`).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_image_descriptions_repository.py`:

```python
"""
Integration tests for repository/image_descriptions.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from repository.image_descriptions import ImageDescriptionsRepository
from Storage.models import Image, ImageDescription


async def _insert_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_save_persists_prompt_key_and_model(db_session):
    image = await _insert_image(db_session)
    repo = ImageDescriptionsRepository(db_session)

    repo.save(image.id, "general_description", "qwen2.5vl:7b", "a meme about cats")
    await db_session.flush()

    result = await db_session.execute(select(ImageDescription).where(ImageDescription.image_id == image.id))
    row = result.scalar_one()
    assert row.prompt_key == "general_description"
    assert row.model_used == "qwen2.5vl:7b"
    assert row.text == "a meme about cats"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_image_ids_with_prompt_returns_only_matching_prompt_key(db_session):
    image_a = await _insert_image(db_session)
    image_b = await _insert_image(db_session)
    repo = ImageDescriptionsRepository(db_session)

    repo.save(image_a.id, "general_description", "llava", "text a")
    repo.save(image_b.id, "humor_explanation", "llava", "text b")
    await db_session.flush()

    ids = await repo.get_image_ids_with_prompt("general_description")

    assert ids == {image_a.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_save_raises_on_duplicate_image_and_prompt_key(db_session):
    image = await _insert_image(db_session)
    repo = ImageDescriptionsRepository(db_session)

    repo.save(image.id, "general_description", "llava", "first")
    await db_session.flush()

    repo.save(image.id, "general_description", "llava", "second")
    with pytest.raises(Exception):
        await db_session.flush()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/integration/test_image_descriptions_repository.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'repository.image_descriptions'` (requires a live test Postgres per `tests/integration/conftest.py`; if none is reachable, confirm the failure is the import error, not a connection error, before proceeding).

- [ ] **Step 3: Rename and rewrite the repository**

```bash
git mv repository/ollama_descriptions.py repository/image_descriptions.py
```

Replace the contents of `repository/image_descriptions.py` (currently importing/using `OllamaDescription`) with:

```python
import uuid

from sqlalchemy import delete, select

from Storage.models import ImageDescription


class ImageDescriptionsRepository:

    def __init__(self, session):
        self.session = session

    async def delete_all(self) -> None:
        await self.session.execute(delete(ImageDescription))

    def save(self, image_id, prompt_key: str, model_used: str, text: str) -> None:
        self.session.add(ImageDescription(
            image_id=image_id, prompt_key=prompt_key, model_used=model_used, text=text,
        ))

    async def get_all_texts(self) -> list[str]:
        result = await self.session.execute(select(ImageDescription.text))
        return result.scalars().all()

    async def get_image_ids_with_prompt(self, prompt_key: str) -> set[uuid.UUID]:
        result = await self.session.execute(
            select(ImageDescription.image_id)
            .where(ImageDescription.prompt_key == prompt_key)
            .distinct()
        )
        return set(result.scalars().all())
```

- [ ] **Step 4: Update `repository/images.py`**

Change the import (line 6):

```python
from Storage.models import OCRText, Image, OllamaDescription, ImageTag
```

to:

```python
from Storage.models import OCRText, Image, ImageDescription, ImageTag
```

Change the aliased attribute (line 14):

```python
        self.description = aliased(OllamaDescription)
```

to:

```python
        self.description = aliased(ImageDescription)
```

Rename `get_images_and_ollama_descriptions` (lines 53-64) to `get_images_and_descriptions` (body unchanged, only the method name changes):

```python
    async def get_images_and_descriptions(self):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.description.text
            ).join(
                self.description, self.description.image_id == self.img.id
            )
        )
        result = await self.session.execute(query)
        return result.fetchall()
```

Rename `get_images_and_ollama_descriptions_without_tags` (lines 66-83) to `get_images_and_descriptions_without_tags` (body unchanged, only the method name changes):

```python
    async def get_images_and_descriptions_without_tags(self, source: str):
        already_tagged = (
            select(ImageTag.image_id)
            .where(ImageTag.source == source)
            .distinct()
            .scalar_subquery()
        )
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.description.text
            )
            .join(self.description, self.description.image_id == self.img.id)
            .where(self.img.id.not_in(already_tagged))
        )
        result = await self.session.execute(query)
        return result.fetchall()
```

Delete the `get_all_images_without_description` method entirely (lines 85-92):

```python
    async def get_all_images_without_description(self):
        has_description = (
            select(OllamaDescription.image_id)
            .distinct()
            .scalar_subquery()
        )
        query = select(Image.filename, Image.id).where(Image.id.not_in(has_description))
        return await self.session.execute(query)
```

(Its only caller is `batch/build_image_descriptions.py`, rewritten in Task 7 to use the new fill-missing-pairs query instead.)

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/integration/test_image_descriptions_repository.py -v
```

Expected: PASS (3 tests). Requires a reachable test Postgres — if unavailable in this environment, note that in the task result instead of skipping verification silently.

- [ ] **Step 6: Commit**

```bash
git add repository/image_descriptions.py repository/images.py tests/integration/test_image_descriptions_repository.py
git commit -m "refactor: rename OllamaDescriptionsRepository to ImageDescriptionsRepository, add prompt-key lookups"
```

---

### Task 6: Update remaining OllamaDescription consumers

**Files:**
- Modify: `Backend/app/repositories/diagnostics_repository.py`
- Modify: `batch/build_tags_from_descriptions.py`
- Modify: `batch/build_bow.py`
- Modify: `tools/agent_untagged.py`
- Modify: `tools/agent_duplicates.py`
- Modify: `batch/experimental/analyse_untagged_descriptions.py`

**Interfaces:**
- Consumes: `ImageDescription` (Task 2), `ImagesRepository.get_images_and_descriptions[_without_tags]` (Task 5), `ImageDescriptionsRepository` (Task 5).

**Note (added after Task 5's implementer flagged it):** the original plan's file list for this task was incomplete — a full-repo grep at plan-writing time missed three more direct `OllamaDescription` ORM-class references (these query the class directly via raw `select()`, not through `ImagesRepository`, which is why they weren't caught alongside the repository-layer renames). Steps 4-6 below cover them; step numbers after the original Step 3 have shifted accordingly from the first-drafted version of this task.

- [ ] **Step 1: Update `Backend/app/repositories/diagnostics_repository.py`**

Change the import (line 4-8):

```python
from Storage.models import (
    Concept, ConceptImage, ConceptImageSet,
    Embedding, Image, ImageExtras,
    ImageTag, OCRText, OllamaDescription, TmpImageClusters, TrendsRun, TrendSource,
)
```

to:

```python
from Storage.models import (
    Concept, ConceptImage, ConceptImageSet,
    Embedding, Image, ImageExtras,
    ImageTag, OCRText, ImageDescription, TmpImageClusters, TrendsRun, TrendSource,
)
```

Change the usage (line 36):

```python
                select(func.count(OllamaDescription.image_id.distinct()))
                    .scalar_subquery().label("with_descriptions"),
```

to:

```python
                select(func.count(ImageDescription.image_id.distinct()))
                    .scalar_subquery().label("with_descriptions"),
```

- [ ] **Step 2: Update `batch/build_tags_from_descriptions.py`**

Change line 27:

```python
            images_and_texts_results = await images_repo.get_images_and_ollama_descriptions_without_tags("Ollama")
```

to:

```python
            images_and_texts_results = await images_repo.get_images_and_descriptions_without_tags("Ollama")
```

Change line 29:

```python
            images_and_texts_results = await images_repo.get_images_and_ollama_descriptions()
```

to:

```python
            images_and_texts_results = await images_repo.get_images_and_descriptions()
```

- [ ] **Step 3: Update `batch/build_bow.py`**

Change the import (line 16):

```python
from repository.ollama_descriptions import OllamaDescriptionsRepository
```

to:

```python
from repository.image_descriptions import ImageDescriptionsRepository
```

Change the instantiation (line 184):

```python
    repo = OllamaDescriptionsRepository(session)
```

to:

```python
    repo = ImageDescriptionsRepository(session)
```

- [ ] **Step 4: Update `tools/agent_untagged.py`, `tools/agent_duplicates.py`, `batch/experimental/analyse_untagged_descriptions.py`**

In `tools/agent_untagged.py`, change line 36:

```python
    from Storage.models import Image, OCRText, OllamaDescription, ImageTag, ImageExtras
```

to:

```python
    from Storage.models import Image, OCRText, ImageDescription, ImageTag, ImageExtras
```

Change lines 67-68:

```python
            select(OllamaDescription.image_id, OllamaDescription.text)
            .where(OllamaDescription.image_id.in_(image_ids))
```

to:

```python
            select(ImageDescription.image_id, ImageDescription.text)
            .where(ImageDescription.image_id.in_(image_ids))
```

In `tools/agent_duplicates.py`, change line 56:

```python
    from Storage.models import OllamaDescription, Embedding
```

to:

```python
    from Storage.models import ImageDescription, Embedding
```

Change lines 100-101:

```python
            select(OllamaDescription.image_id, OllamaDescription.text)
            .where(OllamaDescription.image_id.in_(image_ids))
```

to:

```python
            select(ImageDescription.image_id, ImageDescription.text)
            .where(ImageDescription.image_id.in_(image_ids))
```

In `batch/experimental/analyse_untagged_descriptions.py`, change line 8:

```python
from Storage.models import Image, OllamaDescription, ImageTag
```

to:

```python
from Storage.models import Image, ImageDescription, ImageTag
```

Change lines 36 and 39 (the two other `OllamaDescription` references inside the `select()`/`.where()` call):

```python
                OllamaDescription.text
            )
            .where(
                OllamaDescription.image_id.in_(
```

to:

```python
                ImageDescription.text
            )
            .where(
                ImageDescription.image_id.in_(
```

- [ ] **Step 5: Verify nothing else references the old names**

```bash
grep -rn "OllamaDescription\|get_images_and_ollama_descriptions\|get_all_images_without_description\|ollama_descriptions" --include="*.py" Backend batch repository tests tools
```

Expected: no output (aside from the already-immutable historical migration `Storage/alembic/versions/1d0b68c811bc_added_ollama_descriptions_table.py`, which is out of scope — grep excludes `Storage` above so it won't appear).

- [ ] **Step 6: Run the affected test suites**

```bash
pytest Backend/tests/ -v
pytest tests/integration/test_backend_diagnostics_repository.py -v
python -c "import batch.build_tags_from_descriptions; import batch.build_bow"
python -c "import tools.agent_untagged; import tools.agent_duplicates; import batch.experimental.analyse_untagged_descriptions"
```

Expected: PASS / no import errors. The diagnostics integration test requires a reachable test Postgres, same caveat as Task 5. Note `tools/agent_untagged.py` and `tools/agent_duplicates.py` do their `Storage.models` import inside `main()`, not at module top level, so a bare module import won't actually exercise the changed lines — this command only proves the module itself is syntactically valid. That's still worth running, but it is not a substitute for eyeballing the edited lines directly.

- [ ] **Step 7: Commit**

```bash
git add Backend/app/repositories/diagnostics_repository.py batch/build_tags_from_descriptions.py batch/build_bow.py \
  tools/agent_untagged.py tools/agent_duplicates.py batch/experimental/analyse_untagged_descriptions.py
git commit -m "refactor: update remaining OllamaDescription consumers to ImageDescription"
```

---

### Task 7: Rewrite the batch script — multi-prompt, incremental commit, progress

**Files:**
- Create: `batch/utils/description_batch_commit.py`
- Modify: `batch/build_image_descriptions.py`

**Interfaces:**
- Consumes: `PromptConfig`, `load_prompts`, `resolve_model` (Task 3); `OllamaImageDescriber.describe(path, prompt, model)` (Task 4); `ImageDescriptionsRepository`, `ImagesRepository.get_all_images()` (Task 5); `ProgressTracker`, `SimpleMetricsListener` (pre-existing, unmodified).
- Produces: `DescriptionBatchCommitter` (`save_description(image_id, prompt_key, model_used, text)`, `on_image_done()`, `flush()`, `close()`); `_images_missing_prompts(images_repo, descriptions_repo, prompts) -> list[tuple[str, uuid.UUID, list[PromptConfig]]]`; rewritten CLI entrypoint with `--reset` replacing the old `--incremental`.

- [ ] **Step 1: Write the failing test for the fill-missing-pairs logic**

Create `tests/integration/test_build_image_descriptions.py`:

```python
"""
Integration tests for batch/build_image_descriptions.py's fill-missing-pairs logic.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
"""
import uuid

import pytest

from ai.image_description_prompts import PromptConfig
from batch.build_image_descriptions import _images_missing_prompts
from repository.image_descriptions import ImageDescriptionsRepository
from repository.images import ImagesRepository
from Storage.models import Image


async def _insert_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_images_missing_prompts_returns_only_uncovered_pairs(db_session):
    image_a = await _insert_image(db_session)
    image_b = await _insert_image(db_session)

    descriptions_repo = ImageDescriptionsRepository(db_session)
    descriptions_repo.save(image_a.id, "general_description", "llava", "existing text")
    await db_session.flush()

    prompts = [
        PromptConfig(key="general_description", prompt="What is shown?"),
        PromptConfig(key="humor_explanation", prompt="Explain the joke."),
    ]
    images_repo = ImagesRepository(db_session)

    work = await _images_missing_prompts(images_repo, descriptions_repo, prompts)
    work_by_image = {image_id: missing for _, image_id, missing in work}

    assert {p.key for p in work_by_image[image_a.id]} == {"humor_explanation"}
    assert {p.key for p in work_by_image[image_b.id]} == {"general_description", "humor_explanation"}
    total_missing_pairs = sum(len(missing) for _, _, missing in work)
    assert total_missing_pairs == 3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/integration/test_build_image_descriptions.py -v
```

Expected: FAIL — `ImportError: cannot import name '_images_missing_prompts' from 'batch.build_image_descriptions'` (the module still has its old shape at this point).

- [ ] **Step 3: Create the committer**

Create `batch/utils/description_batch_commit.py`:

```python
from repository.image_descriptions import ImageDescriptionsRepository


class DescriptionBatchCommitter:
    def __init__(self, session, batch_size: int = 100):
        self._session = session
        self._repo = ImageDescriptionsRepository(session)
        self._batch_size = batch_size
        self._pending = 0

    def save_description(self, image_id, prompt_key: str, model_used: str, text: str) -> None:
        self._repo.save(image_id, prompt_key, model_used, text)

    async def on_image_done(self) -> None:
        self._pending += 1
        if self._pending >= self._batch_size:
            await self.flush()

    async def flush(self) -> None:
        await self._session.commit()
        self._pending = 0

    async def close(self) -> None:
        if self._pending > 0:
            await self.flush()
        await self._session.close()
```

This deliberately does not reuse `batch/utils/batch_commit.py`'s `BatchCommitter` — that class is wired specifically to OCR's repositories (`OCRTextRepository`, `ImageMetricsRepository`, `ImageProcessingStatusRepository`); descriptions have no per-pipeline status table (the unique constraint is the "already done" check), so a separate, smaller committer keeps both classes single-purpose.

- [ ] **Step 4: Rewrite `batch/build_image_descriptions.py`**

Replace the entire file:

```python
import argparse
import asyncio
import os

from ai.image_description_prompts import load_prompts, resolve_model
from ai.ollama import OllamaImageDescriber
from batch.utils.description_batch_commit import DescriptionBatchCommitter
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.image_descriptions import ImageDescriptionsRepository
from repository.images import ImagesRepository


async def _load_existing_pairs(descriptions_repo, prompts):
    existing = {}
    for prompt in prompts:
        existing[prompt.key] = await descriptions_repo.get_image_ids_with_prompt(prompt.key)
    return existing


async def _images_missing_prompts(images_repo, descriptions_repo, prompts):
    existing = await _load_existing_pairs(descriptions_repo, prompts)

    result = await images_repo.get_all_images()
    work = []
    for filename, image_id in result:
        missing = [p for p in prompts if image_id not in existing[p.key]]
        if missing:
            work.append((filename, image_id, missing))
    return work


async def main(reset: bool):
    BASE_PATH = settings.BASE_PATH
    print(f"BASE_PATH={BASE_PATH}")
    base_path = os.path.abspath(BASE_PATH)

    prompts_file = settings.get("image_descriptions.prompts_file")
    if not prompts_file:
        raise RuntimeError(
            "image_descriptions.prompts_file is not configured for this environment"
        )
    prompts = load_prompts(prompts_file)
    print(f"Loaded {len(prompts)} prompt(s): {[p.key for p in prompts]}")

    metrics = SimpleMetricsListener()
    describer = OllamaImageDescriber()

    async with AsyncSessionLocal() as session:
        descriptions_repo = ImageDescriptionsRepository(session)
        images_repo = ImagesRepository(session)

        if reset:
            print("Deleting all descriptions...")
            await descriptions_repo.delete_all()
            await session.commit()
            print("Done")

        work = await _images_missing_prompts(images_repo, descriptions_repo, prompts)

        committer = DescriptionBatchCommitter(session, batch_size=settings.GENERAL.BATCH_SIZE)
        tracker = ProgressTracker(total=len(work), report_every=settings.GENERAL.PROGRESS_EVERY)

        for filename, image_id, missing in work:
            path = os.path.join(base_path, filename)

            if path.lower().endswith("webp"):
                print(f"Skipping {path}")
                metrics.increment("skipped.webp")
                tracker.skip()
                continue

            for prompt in missing:
                model = resolve_model(prompt, settings)
                try:
                    text = describer.describe(path, prompt.prompt, model)
                    committer.save_description(image_id, prompt.key, model, text)
                    metrics.increment("saved")
                except Exception as e:
                    print(f"Model failed for {path} [{prompt.key}]: {e}")
                    metrics.increment("error.model")

            await committer.on_image_done()
            tracker.mark_done()

        await committer.close()

    tracker.summary()
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete all existing descriptions before running "
                             "(default: fill only missing image/prompt pairs)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.reset))
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/integration/test_build_image_descriptions.py -v
```

Expected: PASS. Requires a reachable test Postgres — same caveat as Task 5.

- [ ] **Step 6: Import/syntax check**

```bash
python -c "import batch.build_image_descriptions"
python -m batch.build_image_descriptions --help
```

Expected: no traceback; `--help` output lists `--env` and `--reset`.

- [ ] **Step 7: Manual smoke test (requires a running Ollama and a populated `general` environment DB — not runnable in a sandboxed/CI context, do not skip silently, just note if it can't be run here)**

```bash
set WATCHFILES_FORCE_POLLING=1
python -m batch.build_image_descriptions --env general
```

Confirm in the output:
- `Loaded 1 prompt(s): ['general_description']` (or however many are in `batch/data/image-description-prompts.general.yaml` at the time).
- `[done/~total] elapsed=... avg=...s/img eta≈...` progress lines appear periodically.
- Re-running immediately afterward processes zero images (all `(image, prompt)` pairs already filled) — `total` should be `0`.
- Adding a second entry to `batch/data/image-description-prompts.general.yaml` and rerunning processes only that new prompt for existing images, not `general_description` again.

- [ ] **Step 8: Commit**

```bash
git add batch/utils/description_batch_commit.py batch/build_image_descriptions.py tests/integration/test_build_image_descriptions.py
git commit -m "feat: rewrite build_image_descriptions for multi-prompt, incremental commit, and progress reporting"
```

---

## Final verification (after all tasks)

```bash
pytest batch/tests/ -v
pytest tests/ai/ -v
pytest Backend/tests/ -v
pytest tests/integration/ -v   # requires a live test Postgres with pgvector
```

All should pass. Then confirm the CLAUDE.md pre-commit checklist items: server starts without import errors (`uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.general --port 8082`), and `/api/diagnostics/health` / `/api/images?limit=1` still respond.
