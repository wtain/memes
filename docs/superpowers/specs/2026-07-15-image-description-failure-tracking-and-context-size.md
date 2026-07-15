# Image description failure tracking + Ollama context size

## Context

A manual smoke test of `batch/build_image_descriptions.py` against the real
`general` environment (21,954 images, Ollama backend, `qwen2.5vl:7b`)
surfaced two recurring failure classes:

```
Model failed for ...OPxnDPeprpnXoGB3JLknT5uEogoMvrDkhYT7....jpg [general_description]:
  {"error":{"code":400,"message":"Failed to load image or audio file", ...}}
Model failed for ...RyffHBU7bPQwt3wiqF8SPm(1).jpg [general_description]:
  {"error":{"code":400,"message":"request (4124 tokens) exceeds the available
  context size (4096 tokens), try increasing it", "type":"exceed_context_size_error", ...}}
```

A subagent investigation confirmed the root cause of the first class: two
files are WebP images saved with a `.jpg` extension (`file`/PIL both confirm
`RIFF ... Web/P image, VP8 encoding`, fully intact, not corrupted). Ollama's
llava/qwen2.5vl backend (llama.cpp's `clip.cpp`/stb_image loader) cannot
decode WebP at all, so any `.jpg`-by-extension file that's actually WebP
content fails every time. The pipeline's existing `path.lower().endswith("webp")`
skip only checks the file *extension*, so it never catches these. A ~2,000-file
sample of the 21,954-image corpus found no other extension-mismatched files,
so this looks rare corpus-wide, but not zero — and any future unreadable-format
class (corrupt file, unsupported encoding, truncated download) would hit the
same problem.

The second class is a genuine model context-window limit: some
image+prompt combinations produce more tokens than the model's configured
`n_ctx` (4096) allows.

Both classes currently retry forever: a failed `(image, prompt)` pair simply
stays "missing" and gets reattempted on every subsequent run, at real
per-image Ollama cost, with no way to tell "will never succeed as configured"
apart from "hasn't been attempted yet."

This spec covers:
1. Making the model's usable context window larger (config, not code logic).
2. Giving the batch script a way to remember "this pair failed" and stop
   retrying it by default, while still allowing an explicit one-shot retry.

Deliberately out of scope (per discussion): pre-emptively sniffing real
image format via magic bytes to catch mislabeled files before calling
Ollama. The generic failure-tracking mechanism below handles this file class
the same way it handles any other permanent failure — one wasted Ollama
round-trip per bad file, ever, not one per run.

## `num_ctx` config

`OllamaImageDescriber.describe()` gains a `num_ctx: int` parameter, passed
through as an `options` override on the `ollama.chat()` call:

```python
class OllamaImageDescriber:

    def describe(self, path: str, prompt: str, model: str, num_ctx: int) -> str:
        response = ollama.chat(
            model=model,
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [path]
            }],
            options={'num_ctx': num_ctx}
        )
        return response['message']['content']
```

New tracked config key, global only (no per-prompt override — context needs
are a function of image/prompt size, not which prompt is configured):

```yaml
# environments/settings.yaml
image_descriptions:
  model: llava
  num_ctx: 8192
```

`8192` is a starting point, not a proven ceiling — both `llava` and
`qwen2.5vl:7b` are Q4 7B models (~4.5GB weights each per `ollama list`), and
with 12GB of VRAM there's headroom for a larger KV cache beyond the default
4096. If 8192 still isn't enough for some images, this is a one-line config
change, not a code change.

In `batch/build_image_descriptions.py`'s `main()`, read once (it's global,
not per-prompt) and pass through at each `describe()` call site:

```python
num_ctx = settings.get("image_descriptions.num_ctx")
...
text = describer.describe(path, prompt.prompt, model, num_ctx)
```

## Failure tracking

### Reusing `ImageProcessingStatus`

`Storage/models.py` already has a table built for exactly this shape of
problem — used today by the OCR pipeline:

```python
class ImageProcessingStatus(Base):
    __tablename__ = "image_processing_status"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    pipeline = Column(String, primary_key=True)   # e.g. "easyocr:en"
    status = Column(String, nullable=False)       # processing | done | failed
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    error_message = Column(Text)
```

No schema change needed — this spec only writes new `pipeline` string
values into the existing table, keyed as `f"image_description:{prompt.key}"`
(one row per image per prompt that has ever failed). The `image_descriptions`
table's unique constraint remains the sole "succeeded" signal; this table is
only ever consulted for "has this pair failed before."

### New repository methods (no commit)

`repository/image_procesing_status.py`'s existing `mark_failed()` /
`mark_started()` call `session.commit()` internally — reused as-is here,
every single prompt failure would force an immediate commit, breaking
`DescriptionBatchCommitter`'s batch-size-based commit cadence and
contradicting this repo's "repositories don't commit" convention. Rather
than change that existing, OCR-used method, add two new methods to the same
class that don't commit — the description path uses these; OCR's existing
calls are untouched:

```python
async def record_failure(self, image_id, error: str) -> None:
    """No commit — caller controls commit timing via its own batch committer."""
    status = await self.session.get(ImageProcessingStatus, {"image_id": image_id, "pipeline": self.pipeline})
    if status is None:
        status = ImageProcessingStatus(image_id=image_id, pipeline=self.pipeline)
        self.session.add(status)
    status.status = "failed"
    status.error_message = error
    status.finished_at = datetime.utcnow()

async def get_image_ids_with_status(self, status: str) -> set[uuid.UUID]:
    result = await self.session.execute(
        select(ImageProcessingStatus.image_id)
        .where(ImageProcessingStatus.pipeline == self.pipeline, ImageProcessingStatus.status == status)
    )
    return set(result.scalars().all())

async def delete_all(self) -> None:
    """No commit — same convention as record_failure."""
    await self.session.execute(
        delete(ImageProcessingStatus).where(ImageProcessingStatus.pipeline == self.pipeline)
    )
```

### Retry policy: never auto-retry, explicit one-shot override

`_images_missing_prompts` gains a `retry_failed: bool` parameter, and builds
one `ImageProcessingStatusRepository` per prompt (pipeline
`f"image_description:{prompt.key}"`), reused for both the failed-set lookup
and later for recording new failures:

```python
def _status_repos(session, prompts) -> dict[str, ImageProcessingStatusRepository]:
    return {p.key: ImageProcessingStatusRepository(session, f"image_description:{p.key}") for p in prompts}


async def _load_failed_pairs(status_repos, prompts):
    failed = {}
    for prompt in prompts:
        failed[prompt.key] = await status_repos[prompt.key].get_image_ids_with_status("failed")
    return failed


async def _images_missing_prompts(images_repo, descriptions_repo, status_repos, prompts, retry_failed: bool):
    succeeded = await _load_existing_pairs(descriptions_repo, prompts)
    failed = {} if retry_failed else await _load_failed_pairs(status_repos, prompts)

    result = await images_repo.get_all_images()
    work = []
    for filename, image_id in result:
        missing = [
            p for p in prompts
            if image_id not in succeeded[p.key]
            and (retry_failed or image_id not in failed[p.key])
        ]
        if missing:
            work.append((filename, image_id, missing))
    return work
```

`main()` builds the `status_repos` dict once (via `_status_repos`) and
passes it to both `_images_missing_prompts` and the per-image processing
loop, so the same repository instances are reused for recording failures.

New CLI flag:

```python
parser.add_argument("--retry-failed", action="store_true",
                     help="Re-attempt previously-failed image/prompt pairs "
                          "(default: skip pairs that failed on a prior run)")
```

On a per-prompt failure, alongside the existing `metrics.increment("error.model")`:

```python
except Exception as e:
    print(f"Model failed for {path} [{prompt.key}]: {e}")
    metrics.increment("error.model")
    await status_repos[prompt.key].record_failure(image_id, str(e))
```

New metric `skipped.failed`, counting `(image, prompt)` pairs excluded from
`missing` because of a prior failure — separate from `skipped.webp`, for
run-summary visibility into how many pairs are being permanently skipped.

`--reset` also clears `image_processing_status` rows for every configured
`image_description:*` pipeline (not just `image_descriptions` rows) — a
full reset means a genuinely clean slate, including forgetting prior
failures. In `main()`, once `status_repos` is built:

```python
if reset:
    print("Deleting all descriptions...")
    await descriptions_repo.delete_all()
    for status_repo in status_repos.values():
        await status_repo.delete_all()
    await session.commit()
    print("Done")
```

## Testing

- New `tests/integration/test_image_processing_status_repository.py` — this
  repository currently has zero test coverage. Cover `record_failure`
  (writes without committing — caller's `db_session` transaction is what
  persists it) and `get_image_ids_with_status` (filters by both `pipeline`
  and `status`, doesn't leak across pipelines or statuses).
- Extend `tests/integration/test_build_image_descriptions.py`'s
  `_images_missing_prompts` test: a 4th image with a `record_failure`'d
  status for one prompt is excluded from `missing` for that prompt by
  default, and included when `retry_failed=True`.
- Extend `tests/ai/test_ollama.py`: assert `num_ctx` is passed through as
  `options={"num_ctx": ...}` on the `ollama.chat()` call.
- Extend `batch/tests/test_env_loading.py` and
  `Backend/tests/test_config_integration.py` with the new
  `IMAGE_DESCRIPTIONS.NUM_CTX` key (default `8192`, no per-environment
  override).
