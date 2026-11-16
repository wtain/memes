# ML model registry

Every pretrained model the project loads, with its size, precision/quantization, where it is configured and what uses it.
Sizes and quantizations were read on 2026-11-16 from the developer workstation (`ollama list` / `ollama show`, and the file
sizes in the Hugging Face, EasyOCR and Ultralytics caches). Update this file in the same change as any model swap or new model.

**Precision.** Only the Ollama models are quantized. Every model loaded through PyTorch (CLIP, SBERT, TrOCR, GLiNER, YOLO,
EasyOCR) runs at the checkpoint's native precision, FP32 (YOLOv8n and the EasyOCR weights are also shipped as FP32); the code
never calls `.half()` or passes a `torch_dtype`. "Size" for those is the weights file on disk.

## Served by Ollama (quantized)

| Model | Params | Quantization | Size on disk | Context (model / used) | Configured at | Used by |
|---|---|---|---|---|---|---|
| `qwen2.5vl:7b` | 8.3B | Q4_K_M | 6.0 GB | 128k / 8192 | `meme_profiles.vision_model`, `image_descriptions.model` (general) in `environments/settings*.yaml` | `build_image_descriptions` (image descriptions); `build_meme_profiles` vision fallback (no usable text) |
| `qwen2.5:7b-instruct` | 7.6B | Q4_K_M | 4.7 GB | 32k / 8192 | `meme_profiles.text_model`, `parse.model` (metal, general) | `build_meme_profiles` (text-first profiles); query parser `parse/` (live, `POST /api/meme-from-text/search`) |
| `bge-large` (BGE-large-en) | 334M | F16 (not quantized) | 670 MB | 512 / n/a | `taxonomy.embedding_model` | `build_meme_profile_embeddings` (1024-dim situation vectors); retrieval query embedding (`retrieval/`); curator inline re-embed; `build_taxonomy_similarity` |
| `llava:latest` | 7B | Q4_0 | 4.7 GB | 32k / 8192 | `image_descriptions.model` (default in `settings.yaml`; metal and it inherit it) | `build_image_descriptions` where not overridden |
| `qwen2:latest` | 7.6B | Q4_0 | 4.4 GB | 32k / n/a | `ollama.model`, CLI `--ollama-model` | `build_lemma_clusters` cluster naming (`ai/ollama.py`, optional) |

`parse.num_ctx` must equal `meme_profiles.num_ctx` while both share a model, or Ollama reloads it (see `environments/settings.yaml`).
`it` has no `meme_profiles.text_model` or `parse.model`, so it runs neither job.

## Loaded in-process (PyTorch, FP32)

| Model | Params / dim | Size on disk | Source | Configured at | Used by |
|---|---|---|---|---|---|
| CLIP ViT-B-32 (OpenAI weights, via `open_clip`) | ~151M, 512-dim | 605 MB | `timm/vit_base_patch32_clip_224.openai` | `ai/clip.py` defaults (`ViT-B-32`, `openai`); `EMBEDDING_DIM = 512` | `build_image_embeddings` (image vectors), `build_concept_embeddings` and text-to-image search (text vectors) |
| `BAAI/bge-large-en-v1.5` (SBERT) | 335M, 1024-dim | 1.34 GB | Hugging Face | constant `EMBEDDING_MODEL` in the batch scripts | `build_image_description_embeddings`, `build_description_note_embeddings`; `/similar?source=description` and note search |
| `paraphrase-multilingual-MiniLM-L12-v2` | 118M, 384-dim | 471 MB | sentence-transformers | `ai/sbert.py` default; `EMBEDDING_MODEL` in `build_ocr_text_embeddings` | `build_ocr_text_embeddings` (text-heavy duplicate probe); `build_lemma_clusters` (`text_embed_model: sbert`) |
| `microsoft/trocr-base-str` | ~334M | 1.34 GB | Hugging Face | `MODEL_ID` in `batch/trocr_fallback.py` | `extract_text_from_memes` fallback for stylized fonts (CUDA; skipped if unavailable) |
| GLiNER `urchade/gliner_multi-v2.1` (general) | ~0.3B | 1.16 GB | Hugging Face | `trends.model` in `settings.general.yaml` | `trends_batch` entity extraction |
| GLiNER `urchade/gliner_medium-v2.1` (metal) | ~0.2B | 781 MB | Hugging Face | `trends.model` in `settings.metal.yaml` | `trends_batch` entity extraction |
| YOLOv8n (Ultralytics) | 3.2M | 6.5 MB (`batch/yolov8n.pt`, gitignored) | Ultralytics | `ai/yolo.py` | `detect_entities_and_tag` (animal/object tags, source "YOLO") |
| EasyOCR detector `craft_mlt_25k` | n/a | 83 MB | EasyOCR (`~/.EasyOCR/model`) | `batch/extract_text_from_memes.py` | `extract_text_from_memes` text detection (all languages) |
| EasyOCR recognizers `english_g2`, `latin_g2` (Spanish), `cyrillic_g2` (Russian) | n/a | 15 MB each | EasyOCR | same | `extract_text_from_memes` (EN, ES, RU readers, `gpu=True`) |

Parameter counts for CLIP, MiniLM, TrOCR and GLiNER are the published figures, not measured here (the file sizes are measured).

## Not model weights, but related

- `pymorphy3` (RU morphology) and `wordfreq` are dictionary libraries, not neural models.
- `paddleocr` / `paddlepaddle-gpu` are pinned in `batch/requirements.txt`, but no batch script loads them (see the unimportable-`paddleocr` note in `CLAUDE.md`).
- The Hugging Face cache also holds `gpt2*`, `deberta-v3-base` and `mdeberta-v3-base`; nothing in the repository loads them.
- `batch/experimental/detect_embeddings.py` loads `openai/clip-vit-base-patch32` through `transformers`; it is not part of any pipeline.

## Footprint

Ollama weights: about 20.5 GB if all five are pulled (about 11.4 GB for the two Qwen models plus `bge-large` that the live pipeline needs;
`llava` and `qwen2` only matter where `settings.yaml` defaults apply). In-process weights: about 5.8 GB with both GLiNER variants.
Vector dimensions: CLIP 512, bge-large 1024, MiniLM 384 (`Storage/models.py`).
