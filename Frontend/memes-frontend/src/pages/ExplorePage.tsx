import { MemesList } from "../components/MemesList"
import { MemesApi } from "../api/MemesApi"

type ExplorePageProps = {
  memesApi: MemesApi
}

export default function ExplorePage({ memesApi }: ExplorePageProps) {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Explore</h1>

      <MemesList
        memesApi={memesApi}
      />
    </div>
  )
}
