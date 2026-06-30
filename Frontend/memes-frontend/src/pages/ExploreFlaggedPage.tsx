import { MemesList } from "../components/MemesList"
import type { MemesApi } from "../api/MemesApi"

type Props = {
  memesApi: MemesApi
}

export default function ExploreFlaggedPage({ memesApi }: Props) {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Flagged</h1>
      <MemesList memesApi={memesApi} listFlagged={true} />
    </div>
  )
}
