import { useSearchParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import { MemesList } from "../components/MemesList"

type Props = { memesApi: MemesApi }

export default function RecommendationsPage({ memesApi }: Props) {
  const [params, setParams] = useSearchParams()
  const query = params.get("q") ?? ""

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Recommendations</h1>
      <input
        value={query}
        onChange={e => {
          const next = new URLSearchParams(params)
          if (e.target.value) next.set("q", e.target.value)
          else next.delete("q")
          setParams(next)
        }}
        placeholder="Filter recommendations..."
        className="w-full mb-4 rounded-md border px-3 py-2"
      />
      <MemesList memesApi={memesApi} filter={query} listRecommendations />
    </div>
  )
}
