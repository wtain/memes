import type { ErrorKind, Status } from "../../reply/messageSearchReducer"

type Props = {
  status: Status
  fallback: string
  error: ErrorKind | null
  onRetry: () => void
}

const REASONS: Record<string, string> = {
  unavailable: "the language model is not reachable",
  timeout: "the language model took too long",
  invalid_output: "the language model gave an unusable answer",
  breaker_open: "the language model was temporarily skipped after repeated failures",
  embed_unavailable: "the similarity model is not reachable",
  empty_input: "there was nothing to read",
}

function reasonsFor(fallback: string): string {
  return fallback
    .split("+")
    .map((part) => REASONS[part])
    .filter(Boolean)
    .join("; ")
}

export default function StatusBanner({ status, fallback, error, onRetry }: Props) {
  if (status === "reading") {
    return <p role="status" className="rounded-md bg-blue-50 p-3">Reading your message…</p>
  }
  if (status === "degraded") {
    const reasons = reasonsFor(fallback)
    return (
      <div role="status" className="rounded-md bg-yellow-50 p-3">
        <strong>Limited understanding.</strong> A simpler match was used{reasons ? `: ${reasons}` : ""}. Results may be less precise.
      </div>
    )
  }
  if (status === "unavailable") {
    return <p role="status" className="rounded-md bg-gray-100 p-3">Message search isn't available in this environment.</p>
  }
  if (status === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md bg-red-50 p-3">
        <span>
          {error === "validation"
            ? "The search request was rejected. Check the message and your edits, then try again."
            : "Something went wrong while searching. Please try again."}
        </span>
        <button type="button" onClick={onRetry} className="min-h-11 rounded border px-3">Try again</button>
      </div>
    )
  }
  return null
}
