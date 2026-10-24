# R2a spike findings: crop detection approach

Status: draft
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Resolves (partly): OQ-1 (approach), OQ-3 (sample)

Question: which approach proposes the right box often enough to be worth the owner's review time? Throwaway code: `batch/tools/recut_spike/`. Read-only on `general` (sample of 3,000 active images outside the frozen corpus; 30,828 eligible). Owner graded 135 of 136 pooled, blinded items.

## Result

| Approach | Graded | Use as is (2) | 2 or 1 | Bar (60% / 85%) |
|---|---|---|---|---|
| A classical (OpenCV margin trim + inner rectangle) | 40 | 38 (95%) | 40 (100%) | pass |
| B vision-model box (`qwen2.5vl:7b`) | 40 | 11 (28%) | 17 (42%) | fail |
| C = A gated by the vision model ("has extras" before, clean after) | 39 | 37 (95%) | 39 (100%) | pass |
| No proposal (20 images) | 11 "fine as is", 9 skipped | | | no misses seen |

Cost: A 3 ms per image (3,000 in 9 s on 8 processes); B and C about 0.8 s per image per call on the shared GPU.

## Reading the result

- **A is the pick.** The vision model's box is unreliable (it often cuts into the picture); as a gate it added nothing within A's top 40, so it is not worth a GPU call per image yet. YOLOv8 stays out.
- **The evidence covers only A's top band.** All graded A items had confidence 1.0 (cut removes at least 30% of the area, 113 of 3,000 images, 3.8%) and all were `margin` kind. A's other proposals (845 of 958: confidence 0.6 to 0.99, and all 140 `inner` rectangles) are ungraded. A proposes on 32% of images overall, mostly because flat caption bands on ordinary memes look like margins, so the lower bands are probably noisy.
- **Failure that matters (owner):** the grades 1 and 0 are cuts that clip part of the text or an important edge of the picture, which destroys the joke. A rectangle cannot tell a border from content. This is why the human review step stays, and it suggests a cheap guard: reject a proposal whose box cuts through OCR text boxes (to test in R2b).
- Where no cut was proposed in this set, none was needed (consistent with NFR-1), but the sample of 20 is too small to say anything about recall.

## Recommendation for R2b

1. Detector A, with the proposal threshold set where the graded evidence is (removed share of at least 30%) until a second round shows lower bands are acceptable. This also answers OQ-4 for now: drop proposals that remove under 30% of the area.
2. Second labelling round before scheduling: about 60 items from confidence 0.8 to 0.99 and from the `inner` kind, same sheet, to decide whether the threshold can drop.
3. Add and test the OCR-overlap guard (do not propose a box that slices through OCR text).
4. Keep the labelled sample in `evaluation/gold/recut/` as R7's start.
