# Batch execution-order diagrams (Graphviz)

The `.dot` files are the source; the `.svg` files next to them are rendered output and are committed so they can be
viewed without tooling. Conventions: white box = shell-only job, blue = also admin-triggerable (`/admin/batches`),
yellow hexagon = human step, dashed edge = automatic chain, dotted edge = optional or informational.

| Diagram | Use case | Page |
|---|---|---|
| [ingestion](ingestion.svg) | inbox to active library, Tier A/B review, promote, abort | [ingestion.md](../use-cases/ingestion.md) |
| [cleanup-mark-move](cleanup-mark-move.svg) | flag, `move_flagged`, `unregister_deleted_images`, `remove_singletons` | [maintenance-and-cleanup.md](../use-cases/maintenance-and-cleanup.md) |
| [duplicates-refresh](duplicates-refresh.svg) | text-heavy, OCR-text embeddings, `rebuild_duplicates`, `clusterize` | [duplicates-and-clusters.md](../use-cases/duplicates-and-clusters.md) |
| [tagging-and-lemmas](tagging-and-lemmas.svg) | OCR, embeddings, lemmas, tags | [ocr-lemmas-tags.md](../use-cases/ocr-lemmas-tags.md) |
| [description-pipeline](description-pipeline.svg) | the 10-step driver and its dependencies | [descriptions-and-profiles.md](../use-cases/descriptions-and-profiles.md) |
| [concept-discovery](concept-discovery.svg) | bag of words to reviewed concepts | [concept-discovery.md](../use-cases/concept-discovery.md) |
| [job-start-paths](job-start-paths.svg) | shell, admin and scheduler start paths | [execution-model.md](../execution-model.md) |

## Re-rendering

Graphviz is not required: any renderer works (`dot -Tsvg x.dot -o x.svg`). The committed SVGs were produced with the
pure-JS `@viz-js/viz` package (`instance().renderString(src, {format: "svg"})`). Re-render after editing a `.dot`
file so the two stay in sync.
