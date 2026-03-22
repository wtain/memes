import { MemesApi } from "../api/MemesApi"
import { Modal } from "./Modal"
import { MemeDetails } from "./MemeDetails"
import { Meme } from "../types/generated/all"

type Props = {
  meme: Meme
  memesApi: MemesApi
  onClose: () => void
}

export function MemeDetailsModal({ meme, memesApi, onClose }: Props) {
  return (
    <Modal onClose={onClose} title="Meme Details">
      <MemeDetails meme={meme} memesApi={memesApi} />
    </Modal>
  )
}