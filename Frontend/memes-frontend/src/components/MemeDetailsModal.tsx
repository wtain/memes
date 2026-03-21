import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import { Meme } from "../types/meme"
import { TagList } from "./TagList"
import { MemesApi } from "../api/MemesApi"
import MemeCard from "./MemeCard"
import { Concept } from "../types/generated/all"
import { ConceptRow } from "./ConceptRow"

type Props = {
  meme: Meme
  onClose: () => void
  memesApi: MemesApi
}

export function MemeDetailsModal({ meme, onClose, memesApi }: Props) {

  const [similarMemes, setSimilarMemes] = useState<Meme[]>([])
  const [concepts, setConcepts] = useState<Concept[]>([])

  useEffect(() => {
    memesApi.similarMemes(meme.id)
      .then((resp) => setSimilarMemes(resp.items!))
  }, [meme])

  useEffect(() => {
    memesApi.getTopConceptsForImage(meme.id)
      .then((resp) => setConcepts(resp!))
  }, [meme])

  // Close on ESC
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }

    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [onClose])

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = "hidden"
    return () => {
      document.body.style.overflow = "auto"
    }
  }, [])

  return createPortal(
    <div
      className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-50 p-6"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl max-w-5xl w-full max-h-[90vh] overflow-y-auto p-6"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex justify-between items-start mb-4">
          <h2 className="text-xl font-bold">Meme Details</h2>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-black"
          >
            ✕
          </button>
        </div>

        {/* Image */}
        <div className="mb-6">
          <MemeCard
                key={meme.id}
                meme={meme}
                memesApi={memesApi}
              />
        </div>

        {/* Metadata */}
        <div className="space-y-4 text-sm">
          <div>
            <strong>ID:</strong> {meme.id}
          </div>

          <div>
            <strong>Text Lines:</strong>
            <ul className="list-disc ml-6">
              {meme.text.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </div>

          <div>
            <strong>Tags:</strong>
            <TagList tags={meme.tags} />
          </div>

          <div>
            { /** Copied from ConceptsPage */}
            <table className="w-full border">
                    <thead>
                      <tr className="text-left bg-gray-100">
                        <th className="p-3">ID</th>
                        <th className="p-3">Name</th>
                      </tr>
                    </thead>
            
                    <tbody>
                      {concepts.map(concept => (
                        <ConceptRow
                          key={concept.id}
                          concept={concept}
                        />
                      ))}
                    </tbody>
                  </table>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            {similarMemes.map(meme => (
              <MemeCard
                key={meme.id}
                meme={meme}
                memesApi={memesApi}
              />
            ))}
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}
