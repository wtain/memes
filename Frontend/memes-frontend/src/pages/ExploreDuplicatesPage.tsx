import { useSearchParams } from "react-router-dom"
import { MemesList } from "../components/MemesList"
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

      <MemesList
        memesApi={memesApi}
        listDuplicates={true}
        groupByCluster={true}
        initialCursor={initialCursor}
        onCursorChange={handleCursorChange}
      />
    </div>
  )
}
