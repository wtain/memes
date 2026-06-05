import { useEffect, useRef, useState } from "react"
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

  const fetchingForIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (!id || fetchingForIdRef.current === id) return
    fetchingForIdRef.current = id
    memesApi.getConcept(Number(id)).then(concept => {
      if (fetchingForIdRef.current === id) {
        setConcept(concept)
        fetchingForIdRef.current = null
      }
    })
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