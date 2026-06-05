import { useEffect, useRef, useState } from "react"
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

  const fetchingForIdRef = useRef<number | null>(null)
  useEffect(() => {
    if (fetchingForIdRef.current === concept.id) return
    fetchingForIdRef.current = concept.id
    load(concept.id)
  }, [concept.id])

  async function load(id: number) {
    setLoading(true)

    const response = await memesApi.getTopImagesForConcept(id)

    if (fetchingForIdRef.current === id) {
      setMemes(response.items ?? [])
      setLoading(false)
      fetchingForIdRef.current = null
    }
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