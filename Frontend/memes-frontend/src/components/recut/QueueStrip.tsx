import type { RecutProposal } from "../../recut/types"

type Props = {
  items: RecutProposal[]
  openId: string | null
  imageUrl: (imageId: string) => string
  onOpen: (id: string) => void
}

export default function QueueStrip({ items, openId, imageUrl, onOpen }: Props) {
  return (
    <ol className="flex gap-2 overflow-x-auto py-2" aria-label="Proposals waiting for review">
      {items.map((item, index) => (
        <li key={item.id}>
          <button
            type="button"
            aria-label={`Open proposal ${index + 1}`}
            aria-current={item.id === openId ? "true" : undefined}
            onClick={() => onOpen(item.id)}
            className={`block min-h-11 rounded border-2 ${item.id === openId ? "border-blue-600" : "border-transparent"}`}
          >
            <img src={imageUrl(item.cutImageId)} alt="" loading="lazy" className="h-16 w-16 object-cover" />
          </button>
        </li>
      ))}
    </ol>
  )
}
