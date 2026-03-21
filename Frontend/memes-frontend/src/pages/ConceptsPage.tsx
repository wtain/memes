import { useEffect, useState } from "react"
import { ConceptRow } from "../components/ConceptRow"
import { ConceptDetailsModal } from "../components/ConceptDetailsModal"
import { MemesApi } from "../api/MemesApi"
import { Concept } from "../types/generated/all"

type Props = {
  memesApi: MemesApi
}

export default function ConceptsPage({
  memesApi,
}: Props) {
  const [concepts, setConcepts] = useState<Concept[]>([])
  const [selectedConcept, setSelectedConcept] =
    useState<Concept | null>(null)

  useEffect(() => {
    memesApi.listConcepts().then(setConcepts)
  }, [])

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Concepts</h1>

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
              onClick={() => setSelectedConcept(concept)}
            />
          ))}
        </tbody>
      </table>

      {selectedConcept && (
        <ConceptDetailsModal
          concept={selectedConcept}
          memesApi={memesApi}
          onClose={() => setSelectedConcept(null)}
        />
      )}
    </div>
  )
}