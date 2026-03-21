import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import { Meme } from "../types/meme"
import { Concept } from "../types/generated/all"
import { MemesApi } from "../api/MemesApi"
import MemeCard from "./MemeCard"

type Props = {
  concept: Concept
  memesApi: MemesApi
  onClose: () => void
}

export function ConceptDetailsModal({
  concept,
  memesApi,
  onClose,
}: Props) {
  const [memes, setMemes] = useState<Meme[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }

    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [onClose])

  useEffect(() => {
    document.body.style.overflow = "hidden"
    return () => {
      document.body.style.overflow = "auto"
    }
  }, [])

  useEffect(() => {
    load()
  }, [concept.id])

  async function load() {
    setLoading(true)

    const response = await memesApi.getTopImagesForConcept(concept.id)

    setMemes(response.items)
    setLoading(false)
  }

  return createPortal(
    <div
      className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-50 p-6"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl max-w-6xl w-full max-h-[90vh] overflow-y-auto p-6"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex justify-between mb-4">
          <h2 className="text-xl font-bold">
            Concept: {concept.name}
          </h2>

          <button onClick={onClose}>✕</button>
        </div>

        {loading ? (
          <div>Loading...</div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {memes.map(meme => (
              <MemeCard
                key={meme.id}
                meme={meme}
                memesApi={memesApi}
              />
            ))}
          </div>
        )}
      </div>
    </div>,
    document.body
  )
}