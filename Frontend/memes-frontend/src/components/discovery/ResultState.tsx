import type { ReactNode } from "react"

export default function ResultState({ loading, error, onRetry, children }: {
  loading: boolean; error: string | null; onRetry: () => void; children: ReactNode
}) {
  if (error !== null) {
    return (
      <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm">
        <p>{error}</p>
        <button type="button" className="mt-2 min-h-11 rounded border px-3" onClick={onRetry}>Retry</button>
      </div>
    )
  }
  if (loading) return <p role="status" className="text-sm text-gray-600">Loading…</p>
  return <>{children}</>
}
