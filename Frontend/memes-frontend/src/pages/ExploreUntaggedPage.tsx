import { MemesList } from "../components/MemesList"
import { MemesApi } from "../api/MemesApi"

type ExploreUntaggedPageProps = {
  memesApi: MemesApi
}

export default function ExploreUntaggedPage({ memesApi }: ExploreUntaggedPageProps) {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Explore</h1>

      <MemesList
        memesApi={memesApi}
        listUntagged={true}
      />
    </div>
  )
}
