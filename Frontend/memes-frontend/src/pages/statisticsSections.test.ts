import type { StatisticsResponse } from "../types/generated/all"
import { buildSections, changedCellLabels } from "./statisticsSections"

function makeStats(over: { memes?: object; content?: object } = {}): StatisticsResponse {
  return {
    memes: {
      total: 10, pending: 1, rejected: 2, with_embeddings: 9, with_ocr: 8, with_tags: 4,
      without_tags: 6, with_descriptions: 5, with_concept_tags: 3, flagged: 1,
      duplicate_clusters: 2, ocr_missing_text_heavy_classification: 1,
      text_heavy_missing_embeddings: 2, embeddings_missing_lemmas: 3,
      ...over.memes,
    },
    content: {
      ocr_texts: 20, tags: 8, tag_keys: 3, tag_values: 6, concepts: 4, concept_image_sets: 2,
      concept_images: 7, descriptions_approved: 1, descriptions_rejected: 1,
      descriptions_feedback_total: 2,
      ...over.content,
    },
    trends: { runs: 1, trend_sources: 2 },
    computed_at: null,
  }
}

describe("buildSections", () => {
  it("returns the four sections in order", () => {
    expect(buildSections(makeStats()).map((s) => s.title)).toEqual([
      "Library", "Pipeline coverage", "Tags", "Knowledge base",
    ])
  })

  it("formats cells exactly as the page does", () => {
    const [library, pipeline, tags, kb] = buildSections(makeStats())
    expect(library.cells).toEqual([
      { label: "Total memes", value: "10" },
      { label: "Tagged", value: "4 (40.0%)" },
      { label: "Not tagged", value: "6 (60.0%)" },
      { label: "Flagged", value: "1" },
      { label: "Duplicate clusters", value: "2" },
      { label: "Pending ingestion review", value: "1" },
      { label: "Rejected (ingestion)", value: "2" },
    ])
    expect(pipeline.cells).toEqual([
      { label: "With OCR", value: "8 (80.0%)" },
      { label: "With embeddings", value: "9 (90.0%)" },
      { label: "With tags", value: "4 (40.0%)" },
      { label: "With descriptions", value: "5 (50.0%)" },
      { label: "Without descriptions", value: "5 (50.0%)" },
      { label: "With concept assignments", value: "3 (30.0%)" },
      { label: "OCR without text-heavy classification", value: "1" },
      { label: "Text-heavy without OCR-text embeddings", value: "2" },
      { label: "Embeddings without OCR lemmas", value: "3" },
    ])
    expect(tags.cells).toEqual([
      { label: "Total tags", value: "8" },
      { label: "Avg tags / tagged meme", value: "2.0" },
      { label: "Tag categories", value: "3" },
      { label: "Distinct tag values", value: "6" },
      { label: "OCR text blocks", value: "20" },
    ])
    expect(kb.cells).toEqual([
      { label: "Concepts", value: "4" },
      { label: "Concept image sets", value: "2" },
    ])
  })

  it("shows an em dash for average tags when no memes are tagged", () => {
    const tags = buildSections(makeStats({ memes: { with_tags: 0 } }))[2]
    expect(tags.cells[1]).toEqual({ label: "Avg tags / tagged meme", value: "—" })
  })

  it("shows 0.0% when total is zero", () => {
    const library = buildSections(makeStats({ memes: { total: 0, with_tags: 0 } }))[0]
    expect(library.cells[1].value).toBe("0 (0.0%)")
  })

  it("uses unique labels across all sections", () => {
    const labels = buildSections(makeStats()).flatMap((s) => s.cells.map((c) => c.label))
    expect(new Set(labels).size).toBe(labels.length)
  })
})

describe("changedCellLabels", () => {
  it("is empty for identical stats", () => {
    expect(changedCellLabels(makeStats(), makeStats()).size).toBe(0)
  })

  it("lists labels of cells whose formatted value differs", () => {
    const after = makeStats({ memes: { flagged: 5 }, content: { concepts: 9 } })
    expect(changedCellLabels(makeStats(), after)).toEqual(new Set(["Flagged", "Concepts"]))
  })

  it("includes derived cells", () => {
    const after = makeStats({ memes: { with_tags: 5 } })
    const changed = changedCellLabels(makeStats(), after)
    expect(changed.has("Tagged")).toBe(true)
    expect(changed.has("Not tagged")).toBe(true)
    expect(changed.has("With tags")).toBe(true)
    expect(changed.has("Avg tags / tagged meme")).toBe(true)
    expect(changed.has("Flagged")).toBe(false)
  })
})
