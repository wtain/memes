import { Link, useNavigate } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import { useFeature } from "../features/useFeature"
import { Rail } from "../home/Rail"
import { ReplyShortcut } from "../home/ReplyShortcut"
import { SearchHero } from "../home/SearchHero"
import { useHomeFeeds } from "../home/useHomeFeeds"
import { EmptyState, buttonClasses } from "../ui"

type Props = { memesApi: MemesApi }

export default function HomePage({ memesApi }: Props) {
  const navigate = useNavigate()
  const canUpload = useFeature("upload")
  const { recent, trending, retry } = useHomeFeeds(memesApi)

  const settled = recent.status !== "loading" && trending.status !== "loading"
  const candidatesSource = settled ? [...recent.items, ...trending.items] : null
  const nothingToShow = recent.status === "ready" && recent.items.length === 0 &&
    trending.status === "ready" && trending.items.length === 0

  return (
    <div className="pb-12">
      <SearchHero memesApi={memesApi} candidatesSource={candidatesSource} />
      <ReplyShortcut />
      {nothingToShow ? (
        <EmptyState
          title={canUpload ? "No memes yet." : "Nothing here yet, check back soon."}
          action={
            canUpload ? (
              <Link to="/upload" className={buttonClasses({ variant: "primary" })}>
                Upload memes
              </Link>
            ) : undefined
          }
        />
      ) : (
        <>
          <Rail
            title="Recently added"
            seeAllTo="/explore"
            feed={recent}
            memesApi={memesApi}
            onOpenMeme={id => navigate(`/memes/${id}`)}
            onRetry={() => retry("recent")}
            errorMessage="Could not load recent memes."
          />
          <Rail
            title="Trending"
            subtitle="Memes that keep coming back."
            seeAllTo="/trending-memes"
            feed={trending}
            memesApi={memesApi}
            onOpenMeme={id => navigate(`/memes/${id}`)}
            onRetry={() => retry("trending")}
            errorMessage="Could not load trending memes."
          />
        </>
      )}
    </div>
  )
}
