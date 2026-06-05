import { useState } from "react"
import { useParams } from "react-router-dom"
import type { Concept } from "../types/generated/all"
import type { MemesApi } from "../api/MemesApi"
import { ConceptDetails } from "../components/ConceptDetails"
import { useFetchById } from "../utils/useFetchById"

type Props = {
  memesApi: MemesApi
}

export default function ConceptPage({ memesApi }: Props) {
  const { id } = useParams<{ id: string }>()
  const numericId = id ? Number(id) : undefined

  const [concept, setConcept] = useState<Concept | null>(null)

  useFetchById(numericId, n => memesApi.getConcept(n), setConcept)

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
