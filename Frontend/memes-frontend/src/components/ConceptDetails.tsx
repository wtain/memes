import { useEffect, useRef, useState } from "react"
import type { Concept, Meme } from "../types/generated/all"
import type { MemesApi } from "../api/MemesApi"
import MemeCard from "./MemeCard"
import { useNavigate } from "react-router-dom"

type Props = {
  concept: Concept
  memesApi: MemesApi
}

export function ConceptDetails({ concept, memesApi }: Props) {
  const [memes, setMemes] = useState<Meme[]>([])
  // Track which concept ID the current memes belong to so loading is derived,
  // avoiding a synchronous setState call inside the effect body.
  const [loadedForId, setLoadedForId] = useState<number | null>(null)
  const loading = loadedForId !== concept.id
  const navigate = useNavigate()
  const lastFetchedIdRef = useRef<number | undefined>(undefined)
  const currentIdRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    if (lastFetchedIdRef.current === concept.id) return
    lastFetchedIdRef.current = concept.id
    currentIdRef.current = concept.id
    memesApi.getTopImagesForConcept(concept.id).then(response => {
      if (currentIdRef.current === concept.id) {
        setMemes(response.items ?? [])
        setLoadedForId(concept.id)
      }
    })
  }, [concept.id, memesApi])

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
        <div className="h-10 flex items-center justify-center">
          <span>Nothing to show</span>
        </div>
      )}
    </div>
  )
}
