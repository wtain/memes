# P1 vocabulary coverage review

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md` (section 9)
Plan: `docs/superpowers/plans/2026-10-02-p1-taxonomy-and-data-model.md` (Task 5)

## What was run

- Date: 2026-10-02. Environment: `general`, `status = active` images only. Database access through the read-only role (`ocr_readonly`), queries run by the controller session.
- Probe: `batch/tools/probe_vocabulary_coverage.py` (throwaway, not the P2 extractor), model `qwen2.5vl:7b`, `temperature 0`, one prompt per image, JSON output, vocabulary version 1 (draft keys from Appendix A of the spec).
- Requested 100 images stratified 40 / 20 / 20 / 20 by (has OCR, has Ollama description). Received 83 rows, **81 usable**:

| Stratum | Usable | Note |
|---|---|---|
| OCR + description | 40 | |
| OCR only | 20 | |
| description only | 20 | |
| neither | 1 | Only 3 active images in `general` have neither; 2 failed to decode (a GIF and an AVIF, "Failed to load image or audio file") |

## Caveats (read before acting on the numbers)

- 81 images, one prompt, one local 7B vision model. The numbers describe how this probe model labels memes, not ground truth about the corpus. An axis value being unused here does not show it is unneeded.
- The probe model repeated keys it had already assigned inside its free-text `suggested` block, which inflated the "suggested" column of the raw report (for example `amusement` 38, `relate` 21, `friends` 15 are existing keys). The genuine new-value suggestions below were recomputed by counting only values that are not in the axis's vocabulary.
- The corpus is a meme library: humour-related values dominate by construction.
- The probe is a vocabulary sanity check. It measures nothing about retrieval quality (that is P6).

## Findings

Mean values per image against the per-axis limit: emotion 1.17 (limit 3), stance 0.99 (limit 2), topic 0.96 (limit 3), tone 1.00 (limit 2). The limits are not binding.

Share of images with at least one value: emotion 100%, stance 99%, topic 95%, tone 100%.

| Axis | Dominant values (share of 81) | Unused in this sample | Genuinely new suggestions |
|---|---|---|---|
| emotion (26 keys) | `amusement` 70 (86%; 35/40, 19/20, 15/20 across strata) | 12: anxiety, boredom, confusion, contempt, embarrassment, exhaustion, gratitude, indifference, jealousy, resignation, shock, smugness | regret 1, surprise 1, affection 1 |
| stance (15 keys) | `relate` 37 (46%), `mock` 20, `agree` 19 | 9: apologize, brag, complain, congratulate, deflect, demand, refuse, thank, warn | none |
| topic (16 keys) | `friends` 26, `animals` 13, `food` 9, `work` 7, `technology` 7 | 3: holidays, money, weather | religion 2, horror 2, sexuality 1, music 1, art 1 |
| tone (10 keys) | `absurd` 32, `wholesome` 29, `dark` 10 | 4: cynical, dramatic, explicit, self_deprecating | none |

Full raw output of `python -m taxonomy.coverage` is in the Appendix.

Observations:
- `amusement` is nearly the default emotion for memes. It carries little information for ranking, so the right remedy is to down-weight common values at fusion time (P4, inverse-frequency weighting), not to split or remove the key.
- `relate` (46%) is a real and common meme function ("this is me"). It is dominant but meaningful, so keep it.
- The unused stances and emotions are classic reply functions (`refuse`, `thank`, `apologize`, `warn`, `exhaustion`, `resignation`) that the library may hold in small numbers. A 81-image sample from one probe is not strong enough to remove them. Unused keys cost nothing: they only matter for facets, which can hide zero-count values.
- Topic has real gaps. The model repeatedly wanted `religion` and `horror`, and once `music`. `sexuality` overlaps with `tone: explicit` and `art` is vague, so they are not proposed.
- The five definition pairs flagged by the code review as overlapping (annoyance/frustration, boredom/indifference, dark/crude, mock/sarcastic, celebrate/congratulate) do not show a visible problem in this sample (`annoyance` 1 and `frustration` 3 are the only pair with both used). No change proposed. Revisit in P6.

## Proposed vocabulary changes (one adjustment pass)

1. **topic: add `music`, `religion`, `horror`** (16 to 19 keys, inside the lint range 12-20). Evidence is thin (1-2 hits each) but all three are standard meme topics and the cost of an extra key is low. Definitions and labels would be written in the same style as the others.
2. **No other changes.** In particular no key is removed, no key is split and no emotion is added.

The alternative is to apply no changes at all and let P6 decide with a real gold set.

## Decisions

(To be filled after the user review: each change made and each suggestion not taken, with the user's reason.)

## Appendix: raw `python -m taxonomy.coverage` output

See the committed copy below.

# Vocabulary coverage

Images probed: 81

## emotion

- Images with at least one value: 100%
- Unused values: anxiety, boredom, confusion, contempt, embarrassment, exhaustion, gratitude, indifference, jealousy, resignation, shock, smugness
- Overused values (over 40% of images): amusement
- Suggested values the model wanted but the vocabulary lacks: amusement (38), disgust (3), joy (3), frustration (2), sadness (2), fear (2), regret (1), hope (1), pride (1), boredom (1)

| Value | Images |
|---|---|
| amusement | 70 |
| joy | 5 |
| sadness | 4 |
| frustration | 3 |
| excitement | 2 |
| disgust | 2 |
| fear | 2 |
| pride | 1 |
| relief | 1 |
| love | 1 |
| hope | 1 |
| anger | 1 |
| annoyance | 1 |
| disappointment | 1 |
| gratitude | 0 |
| smugness | 0 |
| contempt | 0 |
| resignation | 0 |
| exhaustion | 0 |
| anxiety | 0 |
| embarrassment | 0 |
| confusion | 0 |
| shock | 0 |
| boredom | 0 |
| jealousy | 0 |
| indifference | 0 |

## stance

- Images with at least one value: 99%
- Unused values: apologize, brag, complain, congratulate, deflect, demand, refuse, thank, warn
- Overused values (over 40% of images): relate
- Suggested values the model wanted but the vocabulary lacks: relate (21), mock (14), agree (2), celebrate (1)

| Value | Images |
|---|---|
| relate | 37 |
| mock | 20 |
| agree | 19 |
| celebrate | 2 |
| disagree | 1 |
| console | 1 |
| refuse | 0 |
| congratulate | 0 |
| deflect | 0 |
| warn | 0 |
| complain | 0 |
| apologize | 0 |
| thank | 0 |
| brag | 0 |
| demand | 0 |

## topic

- Images with at least one value: 95%
- Unused values: holidays, money, weather
- Overused values (over 40% of images): none
- Suggested values the model wanted but the vocabulary lacks: friends (15), animals (5), food (4), horror (4), religion (3), technology (2), family (2), health (1), sexuality (1), music (1)

| Value | Images |
|---|---|
| friends | 26 |
| animals | 13 |
| food | 9 |
| work | 7 |
| technology | 7 |
| family | 4 |
| romance | 3 |
| study | 2 |
| health | 2 |
| sleep | 2 |
| gaming | 1 |
| politics | 1 |
| travel | 1 |
| money | 0 |
| holidays | 0 |
| weather | 0 |

## tone

- Images with at least one value: 100%
- Unused values: cynical, dramatic, explicit, self_deprecating
- Overused values (over 40% of images): none
- Suggested values the model wanted but the vocabulary lacks: absurd (14), wholesome (14), dark (3), crude (2), sarcastic (1)

| Value | Images |
|---|---|
| absurd | 32 |
| wholesome | 29 |
| dark | 10 |
| deadpan | 4 |
| crude | 4 |
| sarcastic | 2 |
| dramatic | 0 |
| cynical | 0 |
| self_deprecating | 0 |
| explicit | 0 |

Probe failures (excluded above): 2
