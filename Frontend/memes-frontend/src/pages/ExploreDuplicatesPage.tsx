import { MemesList } from "../components/MemesList"
import type { MemesApi } from "../api/MemesApi"

type ExploreDuplicatesPageProps = {
  memesApi: MemesApi
}

export default function ExploreDuplicatesPage({ memesApi }: ExploreDuplicatesPageProps) {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Explore</h1>

      <MemesList
        memesApi={memesApi}
        listDuplicates={true}
      />
    </div>
  )
}
