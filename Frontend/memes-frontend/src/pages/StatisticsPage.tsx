import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { StatisticsResponse } from "../types/generated/all"

type Props = { memesApi: MemesApi }

function n(v: number | undefined | null): string {
  return (v ?? 0).toLocaleString()
}

function pct(count: number | undefined, total: number | undefined): string {
  if (!total) return "0.0%"
  return `${(((count ?? 0) / total) * 100).toFixed(1)}%`
}

type StatCell = { label: string; value: string }

function StatGrid({ cells }: { cells: StatCell[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {cells.map(({ label, value }) => (
        <div key={label} className="bg-white rounded-lg p-4 shadow-sm">
          <div className="text-sm text-gray-500">{label}</div>
          <div className="text-xl font-semibold mt-1">{value}</div>
        </div>
      ))}
    </div>
  )
}

export default function StatisticsPage({ memesApi }: Props) {
  const [stats, setStats] = useState<StatisticsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const loadedRef = useRef(false)

  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true
    memesApi.getStatistics()
      .then(setStats)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load statistics"))
  }, [memesApi])

  if (error) return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold mb-4">Statistics</h1>
      <p className="text-sm text-red-500">{error}</p>
    </div>
  )

  if (!stats) return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold mb-4">Statistics</h1>
      <p className="text-sm text-gray-400">Loading…</p>
    </div>
  )

  const { memes, content } = stats
  const withoutTags = (memes.total ?? 0) - (memes.with_tags ?? 0)
  const withoutDescriptions = (memes.total ?? 0) - (memes.with_descriptions ?? 0)
  const avgTags = (memes.with_tags ?? 0) > 0
    ? ((content.tags ?? 0) / (memes.with_tags ?? 1)).toFixed(1)
    : "—"

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold mb-4">Statistics</h1>

      <section>
        <h2 className="text-lg font-semibold mb-3">Library</h2>
        <StatGrid cells={[
          { label: "Total memes", value: n(memes.total) },
          { label: "Tagged", value: `${n(memes.with_tags)} (${pct(memes.with_tags, memes.total)})` },
          { label: "Not tagged", value: `${n(withoutTags)} (${pct(withoutTags, memes.total)})` },
          { label: "Excluded", value: n(memes.excluded) },
        ]} />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Pipeline coverage</h2>
        <StatGrid cells={[
          { label: "With OCR", value: `${n(memes.with_ocr)} (${pct(memes.with_ocr, memes.total)})` },
          { label: "With embeddings", value: `${n(memes.with_embeddings)} (${pct(memes.with_embeddings, memes.total)})` },
          { label: "With tags", value: `${n(memes.with_tags)} (${pct(memes.with_tags, memes.total)})` },
          { label: "With descriptions", value: `${n(memes.with_descriptions)} (${pct(memes.with_descriptions, memes.total)})` },
          { label: "Without descriptions", value: `${n(withoutDescriptions)} (${pct(withoutDescriptions, memes.total)})` },
          { label: "With concept assignments", value: `${n(memes.with_concept_tags)} (${pct(memes.with_concept_tags, memes.total)})` },
        ]} />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Tags</h2>
        <StatGrid cells={[
          { label: "Total tags", value: n(content.tags) },
          { label: "Avg tags / tagged meme", value: avgTags },
          { label: "Tag categories", value: n(content.tag_keys) },
          { label: "Distinct tag values", value: n(content.tag_values) },
          { label: "OCR text blocks", value: n(content.ocr_texts) },
        ]} />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Knowledge base</h2>
        <StatGrid cells={[
          { label: "Concepts", value: n(content.concepts) },
          { label: "Concept image sets", value: n(content.concept_image_sets) },
        ]} />
      </section>
    </div>
  )
}