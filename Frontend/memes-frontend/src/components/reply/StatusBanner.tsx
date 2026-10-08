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
    .map((part) => (Object.hasOwn(REASONS, part) ? REASONS[part] : ""))
    .filter(Boolean)
    .join("; ")
}

function InfoMessage({ status, fallback }: Pick<Props, "status" | "fallback">) {
  if (status === "reading") {
    return <p className="rounded-md bg-blue-50 p-3">Reading your message…</p>
  }
  if (status === "degraded") {
    const reasons = reasonsFor(fallback)
    return (
      <div className="rounded-md bg-yellow-50 p-3">
        <strong>Limited understanding.</strong> A simpler match was used{reasons ? `: ${reasons}` : ""}. Results may be less precise.
      </div>
    )
  }
  if (status === "unavailable") {
    return <p className="rounded-md bg-gray-100 p-3">Message search isn't available in this environment.</p>
  }
  return null
}

/**
 * The informational messages live in ONE polite live region that is always mounted (a region that mounts together
 * with its text is announced unreliably); the error keeps role="alert".
 */
export default function StatusBanner({ status, fallback, error, onRetry }: Props) {
  return (
    <>
      <div role="status" aria-live="polite" className="empty:sr-only">
        <InfoMessage status={status} fallback={fallback} />
      </div>
      {status === "error" && <ErrorAlert error={error} onRetry={onRetry} />}
    </>
  )
}

function ErrorAlert({ error, onRetry }: Pick<Props, "error" | "onRetry">) {
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
