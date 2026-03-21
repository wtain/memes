import { Concept } from "../types/generated/all"

type Props = {
  concept: Concept
  onClick?: () => void
}

export function ConceptRow({ concept, onClick }: Props) {
  return (
    <tr
      onClick={onClick}
      className="cursor-pointer hover:bg-gray-100 transition"
    >
      <td className="p-3 border-b">{concept.id}</td>
      <td className="p-3 border-b font-medium">{concept.name}</td>
    </tr>
  )
}