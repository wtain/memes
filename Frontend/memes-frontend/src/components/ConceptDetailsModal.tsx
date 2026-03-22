import { Concept } from "../types/generated/all"
import { MemesApi } from "../api/MemesApi"
import { Modal } from "./Modal"
import { ConceptDetails } from "./ConceptDetails"

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
  return (
    <Modal
      onClose={onClose}
      title={`Concept: ${concept.name}`}
    >
      <ConceptDetails
        concept={concept}
        memesApi={memesApi}
      />
    </Modal>
  )
}