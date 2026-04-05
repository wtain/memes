import { useEffect, useState } from "react"
import { Concept, Meme } from "../types/generated/all"
import { MemesApi } from "../api/MemesApi"
import MemeCard from "./MemeCard"
import { useNavigate } from "react-router-dom"

type Props = {
  concept: Concept
  memesApi: MemesApi
}

export function ConceptDetails({ concept, memesApi }: Props) {
  const [memes, setMemes] = useState<Meme[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    load()
  }, [concept.id])

  async function load() {
    setLoading(true)

    const response = await memesApi.getTopImagesForConcept(concept.id)
    setMemes(response.items ?? [])

    setLoading(false)
  }

  return (
    <div>
      {loading ? (
        <div>Loading...</div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {memes.map(meme => (
            <MemeCard
              key={meme.id}
              meme={meme}
              memesApi={memesApi}
              onClick={() => navigate(`/memes/${meme.id}`)}
            />
          ))}
        </div>
      )}

      {memes.length === 0 && !loading && (
        <div
          className="h-10 flex items-center justify-center"
        >
          <span>Nothing to show</span>
        </div>
      )}
    </div>
  )
}