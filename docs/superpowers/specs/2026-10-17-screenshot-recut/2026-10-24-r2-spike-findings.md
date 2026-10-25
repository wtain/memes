# R2a spike findings: crop detection approach

Status: done
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Resolves (partly): OQ-1 (approach), OQ-3 (sample)

Question: which approach proposes the right box often enough to be worth the owner's review time? The throwaway spike code was deleted when R2 shipped; it is recoverable from commit 6d68dad and its successors on branch `r2-crop-detection`. Read-only on `general` (sample of 3,000 active images outside the frozen corpus; 30,828 eligible). Owner graded 135 of 136 pooled, blinded items.

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

## Round 2: the ungraded bands (62 items, all graded)

| Detector A band | Use as is (2) | 2 or 1 |
|---|---|---|
| margin, confidence 0.6 to 0.7 (cut removes about 7%) | 11 of 12 | 12 of 12 |
| margin, 0.7 to 0.8 (8 to 14%) | 11 of 12 | 11 of 12 |
| margin, 0.8 to 0.9 (16 to 21%) | 12 of 12 | 12 of 12 |
| margin, 0.9 to 1.0 (23 to 30%) | 12 of 12 | 12 of 12 |
| inner rectangle | 2 of 14 | 3 of 14 |

- **Margin trim is good at every confidence**, down to the detector's own floor (7% removed): 46 of 48 as is (96%), 47 of 48 slightly off or better. The 30% threshold I proposed was too cautious and is dropped.
- **The inner-rectangle part fails** (14% as is). It is dropped from R2b; it would need a better method before it can come back.
- Consequence for review load: margin proposals are about 27% of the sampled library (818 of 3,000). Few are wrong, but that is a long queue for one reviewer, so R4 must order by confidence and review in batches.

## Recommendation for R2b

1. Detector A, margin trim only (flat bands peeled from the edges, at least 7% of the area removed, no more than 75% removed). No inner-rectangle proposals. This answers OQ-4: minimum 7% removed, which is the detector's current floor. Smaller trims were not tested.
2. Add and test the OCR-overlap guard (do not propose a box that slices through OCR text): the only failures seen in the margin band are cuts that clip text or an edge.
3. Keep the vision model out of detection. YOLOv8 stays out.
4. The labelled sample (two rounds, 197 judgments) lives in `evaluation/gold/recut/` and is R7's start.
5. Open for the R2b design: whether a trim of less than 7% is worth proposing, and how R4 orders and batches a queue of a few thousand proposals.
