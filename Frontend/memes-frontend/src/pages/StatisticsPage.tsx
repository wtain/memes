import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { StatisticsResponse } from "../types/statistics"

type Props = { memesApi: MemesApi }

function pct(count: number, total: number): string {
  if (total === 0) return "0.0%"
  return `${((count / total) * 100).toFixed(1)}%`
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
  const untagged = memes.total - memes.with_tags
  const avgTags = memes.with_tags > 0
    ? (content.tags / memes.with_tags).toFixed(1)
    : "—"

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold mb-4">Statistics</h1>

      <section>
        <h2 className="text-lg font-semibold mb-3">Library</h2>
        <StatGrid cells={[
          { label: "Total memes", value: memes.total.toLocaleString() },
          { label: "Excluded", value: memes.excluded.toLocaleString() },
        ]} />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Pipeline coverage</h2>
        <StatGrid cells={[
          { label: "With OCR", value: `${memes.with_ocr.toLocaleString()} (${pct(memes.with_ocr, memes.total)})` },
          { label: "With embeddings", value: `${memes.with_embeddings.toLocaleString()} (${pct(memes.with_embeddings, memes.total)})` },
          { label: "With tags", value: `${memes.with_tags.toLocaleString()} (${pct(memes.with_tags, memes.total)})` },
          { label: "Untagged", value: `${untagged.toLocaleString()} (${pct(untagged, memes.total)})` },
          { label: "With descriptions", value: `${memes.with_descriptions.toLocaleString()} (${pct(memes.with_descriptions, memes.total)})` },
          { label: "With concept assignments", value: `${memes.with_concept_tags.toLocaleString()} (${pct(memes.with_concept_tags, memes.total)})` },
        ]} />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Tags</h2>
        <StatGrid cells={[
          { label: "Total tags", value: content.tags.toLocaleString() },
          { label: "Avg tags / tagged meme", value: avgTags },
          { label: "Tag categories", value: content.tag_keys.toLocaleString() },
          { label: "Distinct tag values", value: content.tag_values.toLocaleString() },
          { label: "OCR text blocks", value: content.ocr_texts.toLocaleString() },
        ]} />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Knowledge base</h2>
        <StatGrid cells={[
          { label: "Concepts", value: content.concepts.toLocaleString() },
          { label: "Concept image sets", value: content.concept_image_sets.toLocaleString() },
        ]} />
      </section>
    </div>
  )
}