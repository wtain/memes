import { useState } from "react"
import type { MemesApi } from "../api/MemesApi"

type Props = { memesApi: MemesApi; searchId: string }
type Phase = "idle" | "sending" | "noted" | "failed"

// The explicit miss of a search (epic meme-popularity, S6). Parents render it with key={searchId} so a new search
// starts again from the button.
export default function NotFoundButton({ memesApi, searchId }: Props) {
  const [phase, setPhase] = useState<Phase>("idle")

  async function press() {
    setPhase("sending")
    try {
      await memesApi.reportNotFound(searchId)
      setPhase("noted")
    } catch {
      setPhase("failed")
    }
  }

  if (phase === "noted") return <p role="status" className="py-3 text-sm text-gray-600">Noted</p>
  return (
    <div className="flex flex-wrap items-center gap-2 py-3 text-sm">
      <button type="button" disabled={phase === "sending"} onClick={press} className="min-h-11 rounded border px-3">
        I didn't find it
      </button>
      {phase === "failed" && <span role="status">Couldn't send, try again</span>}
    </div>
  )
}
