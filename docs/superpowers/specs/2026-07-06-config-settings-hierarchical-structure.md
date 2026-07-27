# Config settings: hierarchical YAML structure

Status: done
Originates from: docs/superpowers/specs/2026-07-05-config-management-migration.md

## Problem

Follow-up to `2026-07-05-config-management-migration.md` (implemented). That migration moved tracked config out of `.env` files into flat `environments/settings*.yaml`, but left ~30 keys as a single flat namespace with no indication of which subsystem owns which key — `CLUSTER_SELECTION_METHOD`, `BOW_IGNORE_FILE`, and `CONCEPT_LIMIT` all sit at the same level. This is hard to navigate and will get worse as more keys are added.

## Goals

- Group the ~30 tracked keys into semantically meaningful sections: `ocr`, `rules`, `bow`, `lemma_clustering`, `concepts`, `ollama`, `general`.
- Preserve every currently-resolved value exactly — this is a structural refactor, not a behavior change.
- Establish an exhaustive regression-test safety net for every tracked key's resolution BEFORE restructuring, so the refactor is provably behavior-preserving.
- Update every call site (Backend, Storage, all 19 batch scripts) to the new nested access pattern.
- Update CLAUDE.md, Readme.md, and the original ADR/spec to reflect the new structure.

## Non-goals

- No change to any resolved value, default, or per-environment override.
- Not fixing the pre-existing `PROFILE` vs `TAGGING_PROFILE` naming inconsistency (documented in `docs/adr/adr-2026-07-05-config-management.md`) — `PROFILE` moves to `general.profile` unchanged, still a legacy alias only read by `batch/tools/spot_check_losses.py`.
- No change to `.env.*` secrets layout (`DATABASE_URL`, `BASE_PATH`, `ALTERNATIVE_FRONTEND_ORIGIN`, `SERP_API_KEY`) — this only restructures tracked YAML.
- Not renaming the `environments/settings.<environment>.yaml` files themselves.

## Design

### Grouping rationale

Groups were assigned by grepping every actual call site, not guessed:

| Group | Keys | Owning call sites |
|---|---|---|
| `ocr` | confidence_min, lang_score_min | shared OCR confidence filtering |
| `rules` | file, lemmatize, tagging_data_dir | rules engine (`build_tags_from_ocr.py`, `build_tags_from_descriptions.py`, `build_bow.py` coverage check). `tagging_data_dir` is `ConceptTagger`'s data directory (the new concept-voting rules design, `rules/concept_tagger.py`) — grouped here rather than under `general` since it's engine input, same as `file`, even though it feeds a different sub-engine. |
| `bow` | min_word_length, min_frequency, text_source, output_file, unmatched_file, ignore_file | `build_bow.py` only |
| `lemma_clustering` | min_cluster_size, selection_epsilon, selection_method, text_scope, language, text_embed_model, output_file, min_samples | `build_lemma_clusters.py` only. Named `lemma_clustering` (not `clustering`) to disambiguate from the unrelated image-duplicate `rebuild_duplicates`/`clusterize` batch jobs, which read no config today. |
| `concepts` | lookup, threshold, limit, mapping_file, images_dir, text_concepts_file, text_concepts_templates_file | `tag_images_from_concepts.py`, `build_concept_embeddings.py`, `build_lemma_clusters.py` (lookup) |
| `ollama` | model, enabled | `build_image_descriptions.py`, `build_lemma_clusters.py` (naming) |
| `general` | batch_size, progress_every, tagging_profile, profile, frontend_origin | cross-cutting — confirmed via grep that each of these is read by 2+ unrelated scripts (or, for `frontend_origin`, is a Backend-only CORS value with no natural domain) |

Within a group, the redundant domain prefix is dropped from key names (`RULES_FILE` → `rules.file`, not `rules.RULES_FILE`). Exception: `concepts.text_concepts_file` / `concepts.text_concepts_templates_file` keep the `text_concepts` qualifier — dropping it would collide in meaning with a hypothetical image-concepts file and lose the distinction the current name encodes (per `CLAUDE.md`'s "For image-set concepts, calculates centroids" note).

### Full key mapping

| Old flat key | New path | Present in common `settings.yaml`? |
|---|---|---|
| `OCR_CONFIDENCE_MIN` | `ocr.confidence_min` | yes (0.4) |
| `OCR_LANG_SCORE_MIN` | `ocr.lang_score_min` | yes (0.3) |
| `RULES_LEMMATIZE` | `rules.lemmatize` | yes (false; general overrides true) |
| `RULES_FILE` | `rules.file` | no — undefaulted, per-env only |
| `TAGGING_DATA_DIR` | `rules.tagging_data_dir` | no — undefaulted (falls back to a script-relative path computed in code, not a literal — same as before this migration) |
| `BOW_MIN_WORD_LENGTH` | `bow.min_word_length` | yes (3) |
| `BOW_MIN_FREQUENCY` | `bow.min_frequency` | yes (2) |
| `TEXT_SOURCE` | `bow.text_source` | yes (ocr) |
| `BOW_OUTPUT_FILE` | `bow.output_file` | no — undefaulted, per-env only |
| `BOW_UNMATCHED_FILE` | `bow.unmatched_file` | no — undefaulted, per-env only |
| `BOW_IGNORE_FILE` | `bow.ignore_file` | no — undefaulted, per-env only |
| `MIN_CLUSTER_SIZE` | `lemma_clustering.min_cluster_size` | yes (2) |
| `CLUSTER_SELECTION_EPSILON` | `lemma_clustering.selection_epsilon` | yes (0.0) |
| `CLUSTER_SELECTION_METHOD` | `lemma_clustering.selection_method` | yes (eom; general overrides leaf) |
| `TEXT_SCOPE` | `lemma_clustering.text_scope` | yes (unmatched) |
| `LANGUAGE` | `lemma_clustering.language` | yes (all) |
| `TEXT_EMBED_MODEL` | `lemma_clustering.text_embed_model` | yes (sbert) |
| `CLUSTER_OUTPUT_FILE` | `lemma_clustering.output_file` | no — undefaulted |
| `MIN_SAMPLES` | `lemma_clustering.min_samples` | no — undefaulted |
| `LOOKUP_CONCEPTS` | `concepts.lookup` | yes (false) |
| `CONCEPT_THRESHOLD` | `concepts.threshold` | yes (0.2) |
| `CONCEPT_LIMIT` | `concepts.limit` | yes (50) |
| `CONCEPT_MAPPING_FILE` | `concepts.mapping_file` | no — undefaulted, per-env only |
| `CONCEPT_IMAGES_DIR` | `concepts.images_dir` | no — undefaulted, per-env only |
| `TEXT_CONCEPTS_FILE` | `concepts.text_concepts_file` | no — undefaulted, per-env only |
| `TEXT_CONCEPTS_TEMPLATES_FILE` | `concepts.text_concepts_templates_file` | no — undefaulted, per-env only |
| `OLLAMA_MODEL` | `ollama.model` | yes (qwen2) |
| `OLLAMA_ENABLED` | `ollama.enabled` | yes (true) |
| `BATCH_SIZE` | `general.batch_size` | yes (100) |
| `PROGRESS_EVERY` | `general.progress_every` | yes (10) |
| `TAGGING_PROFILE` | `general.tagging_profile` | no — per-env only (metal/general/it) |
| `PROFILE` | `general.profile` | yes (general) — legacy alias, unchanged behavior |
| `FRONTEND_ORIGIN` | `general.frontend_origin` | yes (http://localhost:5173; metal/general/it override) |

### Resulting tracked YAML

`environments/settings.yaml` (common defaults):
```yaml
ocr:
  confidence_min: 0.4
  lang_score_min: 0.3

rules:
  lemmatize: false

bow:
  min_word_length: 3
  min_frequency: 2
  text_source: ocr

lemma_clustering:
  text_scope: unmatched
  language: all
  min_cluster_size: 2
  selection_epsilon: 0.0
  selection_method: eom
  text_embed_model: sbert

concepts:
  lookup: false
  threshold: 0.2
  limit: 50

ollama:
  model: qwen2
  enabled: true

general:
  batch_size: 100
  progress_every: 10
  profile: general
  frontend_origin: http://localhost:5173
```

`environments/settings.metal.yaml`:
```yaml
general:
  tagging_profile: metal

rules:
  file: data/rules.json

concepts:
  text_concepts_file: data/text-concepts.metal.json
  text_concepts_templates_file: data/text-concepts.templates.metal.json
  images_dir: images
```

`environments/settings.general.yaml`:
```yaml
general:
  tagging_profile: general
  frontend_origin: http://localhost:5174

rules:
  file: data/rules.general.json
  lemmatize: true

bow:
  output_file: output/bow.general.json
  unmatched_file: output/bow.unmatched.general.json
  ignore_file: data/ignore-words.general.json

concepts:
  text_concepts_file: data/text-concepts.general.json
  text_concepts_templates_file: data/text-concepts.templates.general.json
  images_dir: images-general
  mapping_file: data/concepts-to-tags.general.json

lemma_clustering:
  # Empirically validated for this environment's OCR data (see CLAUDE.md's
  # Concept discovery note) — leaf avoids one oversized catch-all cluster.
  selection_method: leaf
```

`environments/settings.it.yaml`:
```yaml
general:
  tagging_profile: it
  frontend_origin: http://localhost:5175
```

### `config/settings.py` change

Add `merge_enabled=True` to the `Dynaconf(...)` constructor in `_build()`:

```python
instance = Dynaconf(
    envvar_prefix=False,
    merge_enabled=True,
    settings_files=[_BASE_SETTINGS_FILE, f"environments/settings.{name}.yaml"],
)
```

This is required because a group like `bow:` is now split across `settings.yaml` (common: `min_word_length`, `min_frequency`, `text_source`) and `settings.general.yaml` (per-env: `output_file`, `unmatched_file`, `ignore_file`). Dynaconf's default multi-file merge is shallow — without `merge_enabled=True`, the later file's `bow:` dict would completely replace the earlier one, silently dropping the common keys.

Verified via a throwaway reproduction (two fixture YAML files, one `bow:` split across both) that `merge_enabled=True` deep-merges correctly with no data loss, and does not reintroduce the earlier `environments=True` cross-environment leakage bug (documented in the ADR) — that bug was about Dynaconf's environment-section mechanism merging data across *all* loaded files regardless of active environment; `merge_enabled` is unrelated, it only deep-merges nested dict *values* within the two files we already deliberately scope to the active environment.

Also verified: `settings.get("RULES.FILE")` (dotted-path string) returns `None` safely even when the whole `rules:` section is absent from both loaded files (e.g. `it`, which sets no `rules:` key at all) — same undefaulted-key behavior as before, just via a dotted path instead of a flat key.

### Access pattern convention (unchanged philosophy, new syntax)

- Bare `settings.GROUP.KEY` for keys guaranteed present in the merged group across all three environments (the "yes" rows in the mapping table above).
- `settings.get("GROUP.KEY")` (dotted-path string) for keys that may be entirely absent for some environment (the "no — undefaulted" rows).

### Migration approach

This is a rename/re-nest of an already-working system, not new infrastructure — same files touched as the original migration. Sequenced to keep the refactor provably safe:

1. **(Sequential, first)** Expand `Backend/tests/test_config_integration.py` and `batch/tests/test_env_loading.py` to assert every tracked key's resolved value per environment (not just the handful checked today), using the CURRENT flat key names. Run and confirm green — this is the required safety-net baseline, committed before any YAML/code changes.
2. **(Sequential, foundational)** Restructure the 4 YAML files per the "Resulting tracked YAML" section above, and add `merge_enabled=True` to `config/settings.py`. Smoke-test nested resolution across all three environments (mirroring the original migration's 3-environment smoke test) before fanning out.
3. **(Parallel subagents)** Update call sites to the new nested paths, split along the same file groups as the original migration (Backend/Storage; batch group A; batch group B) since those boundaries already proved non-overlapping and were reviewed once. Each subagent follows the exact mapping table above — no naming decisions left open to interpretation.
4. **(Sequential, final)** Update the characterization tests from step 1 to use the new nested access paths (`settings.GROUP.KEY` / `settings.get("GROUP.KEY")`) — expected values stay identical, only the access path changes, proving the refactor didn't alter resolution. Run the full test suite (Backend, `tests/rules/`, `batch/tests/`). Update `CLAUDE.md`, `Readme.md`, and cross-reference this spec from the original ADR. Mark this spec `STATUS: IMPLEMENTED`.

### Testing

- `Backend/tests/test_config_integration.py` and `batch/tests/test_env_loading.py` expanded to assert every tracked key's resolved value per environment — this is the primary regression net for the restructuring itself, run both before (flat) and after (nested) with identical expected values.
- Full existing suites must stay green throughout: `cd Backend && pytest` (126+ tests), `pytest tests/rules/` (52 tests), `pytest batch/tests/`.
- No new test *files* beyond what already exists — this expands existing coverage, it doesn't add a new test surface.

## Risks / open questions

- `merge_enabled=True` is a global Dynaconf setting. If a future tracked key is list-valued, deep-merge semantics for lists (append vs. replace) would need separate verification — not a concern today since every tracked key is a scalar or string.
- `config/settings.py`'s docstrings/comments that reference "flat" settings files need a pass to reflect the new nested structure.
- **Discovered during implementation:** the original key-mapping table in this spec omitted `TAGGING_DATA_DIR` (read by `batch/build_tags_from_ocr.py` for `ConceptTagger.load(...)`), found by the subagent migrating batch group A. Grouped under `rules.tagging_data_dir` (added to the tables above) rather than treated as a second cross-cutting/`general` key, since it's engine input for the rules subsystem, same category as `rules.file`.
