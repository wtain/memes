import { MemesList } from "../components/MemesList"
import { MemesApi } from "../api/MemesApi"

type ExplorePageProps = {
  memesApi: MemesApi
  baseUrl: string
}

export default function ExplorePage({ memesApi, baseUrl }: ExplorePageProps) {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Explore</h1>

      <MemesList
        memesApi={memesApi}
        baseUrl={baseUrl}
      />
    </div>
  )
}
