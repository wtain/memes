import { useId } from "react"
import { Link } from "react-router-dom"
import MemeCard from "../components/MemeCard"
import { Button, EmptyState, MemeCardSkeleton, buttonClasses } from "../ui"
import type { MemesApi } from "../api/MemesApi"
import type { Feed } from "./useHomeFeeds"

type RailProps = {
  title: string
  subtitle?: string
  seeAllTo: string
  feed: Feed
  memesApi: MemesApi
  onOpenMeme: (id: string) => void
  onRetry: () => void
  errorMessage: string
}

const SKELETON_COUNT = 6
const CARD_CELL = "w-40 sm:w-44 shrink-0 snap-start"

export function Rail({ title, subtitle, seeAllTo, feed, memesApi, onOpenMeme, onRetry, errorMessage }: RailProps) {
  const headingId = useId()
  if (feed.status === "ready" && feed.items.length === 0) return null

  return (
    <section aria-labelledby={headingId} aria-busy={feed.status === "loading" ? true : undefined} className="mt-8">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 id={headingId} className="text-xl font-semibold text-fg">{title}</h2>
          {subtitle && <p className="text-sm text-fg-muted">{subtitle}</p>}
        </div>
        <Link to={seeAllTo} className={buttonClasses({ variant: "ghost", size: "sm" })}>
          See all
        </Link>
      </div>
      {feed.status === "error" ? (
        <EmptyState
          tone="error"
          title={errorMessage}
          action={<Button onClick={onRetry}>Try again</Button>}
        />
      ) : (
        <div className="mt-4 flex snap-x snap-proximity gap-4 overflow-x-auto pb-2">
          {feed.status === "loading"
            ? Array.from({ length: SKELETON_COUNT }, (_, i) => (
                <div key={i} className={CARD_CELL}>
                  <MemeCardSkeleton />
                </div>
              ))
            : feed.items.map(meme => (
                <div key={meme.id} className={CARD_CELL}>
                  <MemeCard meme={meme} memesApi={memesApi} onClick={() => onOpenMeme(meme.id)} />
                </div>
              ))}
        </div>
      )}
    </section>
  )
}
