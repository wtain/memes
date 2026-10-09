import { Link } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import { useProfileCoverage } from "../curator/useProfileCoverage"
import { CATEGORY_CHOICES } from "../curator/types"
import type { ProfileCoverage } from "../curator/types"

type Props = { memesApi: MemesApi }

function formatTime(value: string | null): string {
  if (!value) return "not finished"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="text-lg font-semibold">{value}</dd>
    </div>
  )
}

type Run = ProfileCoverage["lastRuns"]["profiles"]

function nested(stats: { [k: string]: unknown } | null | undefined, key: string): unknown {
  const coverage = stats?.coverage
  return coverage !== null && typeof coverage === "object" ? (coverage as Record<string, unknown>)[key] : undefined
}

function numberText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "-"
}

function RunBlock({ title, run, children }: { title: string; run: Run; children: (stats: { [k: string]: unknown }) => React.ReactNode }) {
  return (
    <section className="rounded border bg-white p-4" aria-label={title}>
      <h2 className="font-medium mb-2">{title}</h2>
      {run === null ? (
        <p className="text-sm text-gray-600">No run yet</p>
      ) : run.status === "completed" && run.stats !== null ? (
        <>
          <p className="text-xs text-gray-500 mb-2">Completed: {formatTime(run.completedAt)}</p>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">{children(run.stats)}</dl>
        </>
      ) : run.status === "started" ? (
        <>
          <p className="text-xs text-gray-500 mb-2">running since {formatTime(run.startedAt)}</p>
          <p className="text-sm text-gray-700">A run is in progress: numbers appear when it finishes</p>
        </>
      ) : (
        <p className="text-xs text-gray-500">
          Status: {run.status}, started {formatTime(run.startedAt)}, finished {formatTime(run.completedAt)}
        </p>
      )}
    </section>
  )
}

function LastRuns({ coverage }: { coverage: ProfileCoverage }) {
  return (
    <>
      <RunBlock title="Profiles run" run={coverage.lastRuns.profiles}>
        {(stats) => (
          <>
            <Stat label="Stale" value={numberText(nested(stats, "stale"))} />
            <Stat label="Needs review" value={numberText(nested(stats, "needs_review"))} />
            <Stat label="Failed" value={numberText(nested(stats, "failed"))} />
            {typeof stats.images_remaining === "number" && <Stat label="Still to profile" value={String(stats.images_remaining)} />}
          </>
        )}
      </RunBlock>
      <RunBlock title="Embeddings run" run={coverage.lastRuns.embeddings}>
        {(stats) => (
          <>
            <Stat label="Embedded" value={numberText(stats.embedded)} />
            {typeof stats.images_remaining === "number" && <Stat label="Still to embed" value={String(stats.images_remaining)} />}
          </>
        )}
      </RunBlock>
    </>
  )
}

export default function ProfileCoveragePage({ memesApi }: Props) {
  const view = useProfileCoverage(memesApi)
  const { coverage, category } = view
  const choice = CATEGORY_CHOICES.find((c) => c.key === category)
  const selectedCount = coverage !== null && choice ? choice.count(coverage) : null

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Profile coverage</h1>

      <p role="status" className="text-sm text-gray-700">
        {choice && selectedCount !== null ? `${choice.label}: ${selectedCount} memes` : ""}
      </p>

      {view.failed && (
        <div role="alert" className="rounded border border-red-300 bg-red-50 p-4 text-red-800">
          <p>Couldn't load the coverage</p>
          <button
            type="button"
            className="mt-2 min-h-11 rounded bg-red-600 px-4 text-white"
            onClick={view.reload}
          >
            Try again
          </button>
        </div>
      )}

      {coverage === null && !view.failed && <p className="text-sm text-gray-500">Loading…</p>}

      {coverage !== null && (
        <>
          <section className="rounded border bg-white p-4" aria-labelledby="live-heading">
            <h2 id="live-heading" className="font-medium mb-2">Right now</h2>
            <dl className="grid grid-cols-2 gap-4">
              <Stat label="Active memes" value={String(coverage.active)} />
              <Stat label="Profiled" value={String(coverage.profiled)} />
            </dl>
          </section>

          <LastRuns coverage={coverage} />

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {CATEGORY_CHOICES.map((c) => (
              <button
                key={c.key}
                type="button"
                aria-pressed={c.key === category}
                onClick={() => view.select(c.key)}
                className={`min-h-11 rounded border p-3 text-left ${
                  c.key === category ? "border-blue-600 bg-blue-50" : "bg-white"
                }`}
              >
                <span className="block text-sm text-gray-700">{c.label}</span>
                <span className="block text-xl font-semibold">{c.count(coverage)}</span>
              </button>
            ))}
          </div>
        </>
      )}

      <section aria-label="Memes in this category">
        {view.listFailed && (
          <div>
            <p role="alert" className="text-red-700">Couldn't load the memes</p>
            <button type="button" className="mt-2 min-h-11 rounded bg-red-600 px-4 text-white" onClick={() => view.select(category)}>
              Try again
            </button>
          </div>
        )}
        {!view.loading && !view.listFailed && view.items.length === 0 && (
          <p className="text-gray-600">Nothing in this category</p>
        )}
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {view.items.map((item) => (
            <li key={item.id} className="relative">
              <Link to={`/memes/${item.id}`} className="block">
                <img
                  src={memesApi.getImageUrlById(item.id)}
                  alt={`Meme ${item.id.slice(0, 8)}`}
                  loading="lazy"
                  className="w-full rounded border bg-white object-cover"
                />
              </Link>
              {item.humanVerified && (
                <span className="absolute left-1 top-1 rounded bg-green-600 px-1.5 py-0.5 text-xs text-white">
                  Verified
                </span>
              )}
            </li>
          ))}
        </ul>
        {view.loading && <p className="mt-3 text-sm text-gray-500">Loading…</p>}
        {view.hasMore && !view.loading && (
          <button
            type="button"
            className="mt-4 min-h-11 rounded bg-blue-600 px-4 text-white"
            onClick={view.loadMore}
          >
            Show more
          </button>
        )}
      </section>
    </div>
  )
}
