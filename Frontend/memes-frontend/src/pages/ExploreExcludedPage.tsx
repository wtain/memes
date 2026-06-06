import { MemesList } from "../components/MemesList"
import type { MemesApi } from "../api/MemesApi"

type Props = {
  memesApi: MemesApi
}

export default function ExploreExcludedPage({ memesApi }: Props) {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Excluded</h1>
      <MemesList memesApi={memesApi} listExcluded={true} />
    </div>
  )
}
