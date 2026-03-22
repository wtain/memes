import { useEffect, useState } from "react"
import { useParams } from "react-router-dom"
import { Concept } from "../types/generated/all"
import { MemesApi } from "../api/MemesApi"
import { ConceptDetails } from "../components/ConceptDetails"

type Props = {
  memesApi: MemesApi
}

export default function ConceptPage({ memesApi }: Props) {
  const { id } = useParams<{ id: string }>()

  const [concept, setConcept] = useState<Concept | null>(null)

  useEffect(() => {
    if (!id) return

    // you may already have this endpoint or need to add it
    memesApi.getConcept(Number(id)).then(setConcept)
  }, [id])

  if (!concept) {
    return <div>Loading...</div>
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="text-2xl font-bold mb-4">
        Concept: {concept.name}
      </h1>

      <ConceptDetails
        concept={concept}
        memesApi={memesApi}
      />
    </div>
  )
}