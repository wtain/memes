import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import { MemeDetails } from "../components/MemeDetails"
import ProfileEditor from "../components/curator/ProfileEditor"
import type { Meme } from "../types/generated/all"
import { useFeature } from "../features/useFeature"
import { useFetchById } from "../utils/useFetchById"
import { Button, EmptyState, Skeleton, buttonClasses } from "../ui"

type Props = {
  memesApi: MemesApi
}

type Loaded =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; meme: Meme }

// Keyed by `${id}:${attempt}` in MemePage, so a new id or a retry remounts it:
// state resets (no flash of the previous meme) and useFetchById's ref guard resets.
function MemeLoader({ id, memesApi, onRetry }: { id: string; memesApi: MemesApi; onRetry: () => void }) {
  const [state, setState] = useState<Loaded>({ status: "loading" })
  const curator = useFeature("curator")

  useFetchById(
    id,
    i => memesApi.getMeme(i),
    meme => setState({ status: "ready", meme }),
    () => setState({ status: "error" }),
  )

  if (state.status === "loading") {
    return (
      <div role="status" className="max-w-5xl mx-auto">
        <span className="sr-only">Loading...</span>
        <Skeleton className="aspect-square w-full" />
        <Skeleton className="mt-4 h-5 w-1/2" />
        <Skeleton className="mt-2 h-5 w-1/3" />
      </div>
    )
  }

  if (state.status === "error") {
    return (
      <EmptyState
        tone="error"
        title="Could not load this meme"
        description="It may have been removed, or the connection failed."
        action={
          <div className="flex items-center gap-2">
            <Button onClick={onRetry}>Try again</Button>
            <Link to="/explore" className={buttonClasses({ variant: "ghost" })}>Back to Explore</Link>
          </div>
        }
      />
    )
  }

  const { meme } = state
  return (
    <div className="max-w-7xl mx-auto">
      <h1 className="text-2xl font-bold mb-4">
        Meme {meme.id}
      </h1>

      <MemeDetails meme={meme} memesApi={memesApi} />

      {curator && <ProfileEditor key={meme.id} memesApi={memesApi} imageId={meme.id} />}
    </div>
  )
}

export default function MemePage({ memesApi }: Props) {
  const { id } = useParams<{ id: string }>()
  const [attempt, setAttempt] = useState(0)

  if (id === undefined) return null

  return (
    <MemeLoader
      key={`${id}:${attempt}`}
      id={id}
      memesApi={memesApi}
      onRetry={() => setAttempt(a => a + 1)}
    />
  )
}
