import type { StatisticsResponse } from "../types/generated/all"

export type StatCell = { label: string; value: string }
export type StatSection = { title: string; cells: StatCell[] }

function n(v: number | undefined | null): string {
  return (v ?? 0).toLocaleString()
}

function pct(count: number | undefined, total: number | undefined): string {
  if (!total) return "0.0%"
  return `${(((count ?? 0) / total) * 100).toFixed(1)}%`
}

export function buildSections(stats: StatisticsResponse): StatSection[] {
  const { memes, content } = stats
  const withoutTags = (memes.total ?? 0) - (memes.with_tags ?? 0)
  const withoutDescriptions = (memes.total ?? 0) - (memes.with_descriptions ?? 0)
  const avgTags = (memes.with_tags ?? 0) > 0
    ? ((content.tags ?? 0) / (memes.with_tags ?? 1)).toFixed(1)
    : "—"

  return [
    {
      title: "Library",
      cells: [
        { label: "Total memes", value: n(memes.total) },
        { label: "Tagged", value: `${n(memes.with_tags)} (${pct(memes.with_tags, memes.total)})` },
        { label: "Not tagged", value: `${n(withoutTags)} (${pct(withoutTags, memes.total)})` },
        { label: "Flagged", value: n(memes.flagged) },
        { label: "Duplicate clusters", value: n(memes.duplicate_clusters) },
        { label: "Pending ingestion review", value: n(memes.pending) },
        { label: "Rejected (ingestion)", value: n(memes.rejected) },
      ],
    },
    {
      title: "Pipeline coverage",
      cells: [
        { label: "With OCR", value: `${n(memes.with_ocr)} (${pct(memes.with_ocr, memes.total)})` },
        { label: "With embeddings", value: `${n(memes.with_embeddings)} (${pct(memes.with_embeddings, memes.total)})` },
        { label: "With tags", value: `${n(memes.with_tags)} (${pct(memes.with_tags, memes.total)})` },
        { label: "With descriptions", value: `${n(memes.with_descriptions)} (${pct(memes.with_descriptions, memes.total)})` },
        { label: "Without descriptions", value: `${n(withoutDescriptions)} (${pct(withoutDescriptions, memes.total)})` },
        { label: "With concept assignments", value: `${n(memes.with_concept_tags)} (${pct(memes.with_concept_tags, memes.total)})` },
        { label: "OCR without text-heavy classification", value: n(memes.ocr_missing_text_heavy_classification) },
        { label: "Text-heavy without OCR-text embeddings", value: n(memes.text_heavy_missing_embeddings) },
        { label: "Embeddings without OCR lemmas", value: n(memes.embeddings_missing_lemmas) },
      ],
    },
    {
      title: "Tags",
      cells: [
        { label: "Total tags", value: n(content.tags) },
        { label: "Avg tags / tagged meme", value: avgTags },
        { label: "Tag categories", value: n(content.tag_keys) },
        { label: "Distinct tag values", value: n(content.tag_values) },
        { label: "OCR text blocks", value: n(content.ocr_texts) },
      ],
    },
    {
      title: "Knowledge base",
      cells: [
        { label: "Concepts", value: n(content.concepts) },
        { label: "Concept image sets", value: n(content.concept_image_sets) },
      ],
    },
  ]
}

export function changedCellLabels(before: StatisticsResponse, after: StatisticsResponse): Set<string> {
  const previous = new Map<string, string>()
  for (const section of buildSections(before)) {
    for (const cell of section.cells) previous.set(cell.label, cell.value)
  }
  const changed = new Set<string>()
  for (const section of buildSections(after)) {
    for (const cell of section.cells) {
      if (previous.get(cell.label) !== cell.value) changed.add(cell.label)
    }
  }
  return changed
}
