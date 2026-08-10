import { useSearchParams } from "react-router-dom"
import { MemesDuplicatesList } from "../components/MemesDuplicatesList"
import type { MemesApi } from "../api/MemesApi"

type ExploreDuplicatesPageProps = {
  memesApi: MemesApi
}

export default function ExploreDuplicatesPage({ memesApi }: ExploreDuplicatesPageProps) {
  const [params, setParams] = useSearchParams()
  const initialCursor = params.get("cursor") ?? undefined

  function handleCursorChange(cursor: string | undefined) {
    setParams(cursor ? { cursor } : {}, { replace: true })
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Explore</h1>

      <MemesDuplicatesList
        memesApi={memesApi}
        initialCursor={initialCursor}
        onCursorChange={handleCursorChange}
      />
    </div>
  )
}
