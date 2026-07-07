import { MemesList } from "../components/MemesList"
import type { MemesApi } from "../api/MemesApi"

type Props = {
  memesApi: MemesApi
}

export default function ExploreNoOcrPage({ memesApi }: Props) {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">No OCR</h1>
      <MemesList memesApi={memesApi} listNoOcr={true} />
    </div>
  )
}
