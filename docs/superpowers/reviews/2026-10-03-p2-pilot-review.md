# P2 pilot review: choosing the meme profile extraction model

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-03-p2-meme-profile-extraction-design.md` (decision 9)
Plan: `docs/superpowers/plans/2026-10-03-p2-meme-profile-extraction.md` (Task 7)

## What was run

- Date 2026-10-03, environment `general`, active images only. Tooling: `python -m batch.tools.meme_profile_pilot` (`sample`, `run`, `sheet`, `decide`).
- **Sample:** 40 images drawn with a fixed seed through the read-only role: 20 `ocr+description`, 10 `ocr only`, 5 `description only`, 5 `vision` (no usable text, below `min_text_chars` = 40). Text-path items 35, vision-path items 5.
- **Candidates:** text path `qwen2:latest`, `qwen2.5vl:7b` (text only, no image) and `qwen2.5:7b-instruct` (pulled for the pilot with the user's approval, 4.7 GB); vision path `qwen2.5vl:7b` with the image. Production prompt and schema (`PROMPT_VERSION` from `ai/meme_profile_prompt.py`), `temperature 0`, `seed 42`, `num_ctx 8192`, two runs per model and image.
- **Rating:** a blinded side-by-side sheet (labels shuffled per image, model names verified absent from the HTML). The user rated all 35 text-path outputs per model and the 5 vision outputs as best / acceptable / poor (2 / 1 / 0). Raw results, the sheet, the label map and the ratings file contain file names from the user's library and were deliberately not committed.

## Results

| Model (path) | Valid output | Stability | Mean seconds per call | Mean rating (0-2) | best / acceptable / poor |
|---|---|---|---|---|---|
| `qwen2.5:7b-instruct` (text) | 100% (70 runs) | 0.83 | 3.6 (median 2.0) | **0.86** | 8 / 14 / 13 |
| `qwen2:latest` (text) | 100% (70 runs) | 0.74 | 4.1 (median 2.4) | 0.77 | 4 / 19 / 12 |
| `qwen2.5vl:7b` (text only) | 100% (70 runs) | 0.94 | 0.9 (median 0.5) | 0.66 | 7 / 9 / 19 |
| `qwen2.5vl:7b` (vision) | 60% (10 runs) | 0.60 | 0.9 | 0.60 | 0 / 3 / 2 |

Stability is the share of images where both runs were valid and produced identical axis keys. Valid output was 100% for every text model, as expected from schema-constrained decoding, so the human ratings decided.

Mean rating per input group (n):

| Model | ocr+description (20) | ocr only (10) | description only (5) |
|---|---|---|---|
| `qwen2.5:7b-instruct` | 1.10 | 0.40 | 0.80 |
| `qwen2:latest` | 1.00 | 0.40 | 0.60 |
| `qwen2.5vl:7b` (text only) | 1.00 | 0.30 | 0.00 |

On images with both an OCR text and a description, `qwen2.5:7b-instruct` was the sole top-rated output on 6 images, against 3 each for the other two (ties excluded).

## Decision

- **Text model: `qwen2.5:7b-instruct`**, by the decision rule (valid-output rate at least 98%, then highest mean rating; ties to the faster model). It is the best or tied-best in every input group.
- **Vision model: keep `qwen2.5vl:7b`.** The decision rule returns no vision winner because only one candidate ran and its valid-output rate (60%) is below 98%. The four failures were two images Ollama cannot decode ("Failed to load image or audio file"; the known WebP-saved-as-JPG case in CLAUDE.md), which the job records as permanent failures. They say little about the model's quality on decodable images. All five vision outputs were rated (3 acceptable, 2 poor), so the two undecodable images' "invalid output" entries most probably account for the two poor ratings, and the vision mean of 0.60 is understated by them.
- Applied in `environments/settings.metal.yaml` and `environments/settings.general.yaml` (`meme_profiles.text_model`); `environments/settings.yaml` keeps `vision_model: qwen2.5vl:7b` with a comment.

## Findings that matter beyond the choice

1. **Quality is modest overall.** The winner's mean rating is 0.86 of a possible 2: of its 35 rated outputs, 8 were best, 14 acceptable and 13 poor. The profiles are usable as a coarse signal but not trustworthy per image. P4 should lean on controlled-axis agreement and weight by confidence, and P6's gold set is the real test.
2. **OCR-only images are the weak spot** (mean 0.3-0.4 for every model, against about 1.0 with a description). This confirms the spec's risk: with no description, a text model has little to go on. `metal` has most of its images in this situation (only 7,482 of 18,088 have descriptions). Running `build_image_descriptions` on `metal` first would likely improve its profiles more than any model change; this is a follow-up for the user to decide.
3. **Description-only outputs from the vision model's text path were poor** (`qwen2.5vl:7b` text-only, 0.00 on 5 images): a reason not to pick the already-loaded vision model for the text path just to avoid a model swap.

## Throughput and backlog estimate (NFR-5)

- Per-call times come from a run that alternated between three models for each image, so every mean includes some model load time. The medians (about 2.0 s for the winner) are closer to steady state. The pilot ran serially: **concurrency 2 and VRAM behaviour were not measured here**; the first rollout slice (plan Task 8) measures them with `ollama ps`.
- Estimated time to profile every active image with `qwen2.5:7b-instruct`, serially at the median (2.0 s) and at the mean (3.6 s), then with the 25% saving the description job measured for 2 concurrent requests: `general` 32,328 images: 18.0 h to 32.3 h serial, about 13.5 h to 24.2 h at concurrency 2; `metal` 18,088 images: 10.0 h to 18.1 h serial, about 7.5 h to 13.6 h at concurrency 2.
- Slice size: to finish one `description_pipeline` slice in under an hour at the pessimistic mean, `meme_profiles.max_per_run` of about 1,000 would fit (3,600 s / (3.6 s x 0.75) is about 1,333 images). The setting is left unset.

## Caveats

- 40 images, one prompt, ratings by one person on one afternoon. They are the only quality signal until P6 provides a gold set, and OQ-3 stays open there.
- The sample is drawn from `general`; `metal` has a different mix of input richness.
- The two undecodable images cap what the pilot could say about the vision path (3 usable outputs).
