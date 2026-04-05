import { useEffect, useState } from "react"
import { TagList } from "./TagList"
import { MemesApi } from "../api/MemesApi"
import MemeCard from "./MemeCard"
import { Concept, Meme } from "../types/generated/all"
import { ConceptRow } from "./ConceptRow"
import { useNavigate } from "react-router-dom"

type Props = {
  meme: Meme
  memesApi: MemesApi
}

export function MemeDetails({ meme, memesApi }: Props) {
  const [similarMemes, setSimilarMemes] = useState<Meme[]>([])
  const [concepts, setConcepts] = useState<Concept[]>([])
  const navigate = useNavigate()

  useEffect(() => {
    memesApi.similarMemes(meme.id)
      .then(resp => setSimilarMemes(resp.items ?? []))
  }, [meme.id])

  useEffect(() => {
    memesApi.getTopConceptsForImage(meme.id)
      .then(resp => setConcepts(resp ?? []))
  }, [meme.id])

  return (
    <div>
      {/* Image */}
      <div className="mb-6">
        <MemeCard meme={meme} memesApi={memesApi} />
      </div>

      {/* Metadata */}
      <div className="space-y-4 text-sm">
        <div>
          <strong>ID:</strong> <a href={`/memes/${meme.id}`}>{meme.id}</a> <br />
            
                <div onClick={() => {
                        if ("clipboard" in navigator) {
                            navigator.clipboard
                                .writeText(meme.originalFileName!)
                                .then(() => {
                                    
                                })
                        }
                    }}
                    className="cursor-pointer hover:bg-gray-100 transition"
                >
                    <strong>File name: </strong> 
                    {meme.originalFileName} <br />
                </div>
        
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

        {/* Concepts */}
        <div>
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
                  onClick={() => navigate(`/concepts/${concept.id}`)}
                />
              ))}
            </tbody>
          </table>
        </div>

        {/* Similar memes */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {similarMemes.map(m => (
            <MemeCard
              key={m.id}
              meme={m}
              memesApi={memesApi}
              onClick={() => navigate(`/memes/${m.id}`)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}