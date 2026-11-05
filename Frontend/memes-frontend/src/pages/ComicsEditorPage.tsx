import { useParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"

type Props = { memesApi: MemesApi }

// Placeholder: the editor itself arrives with the next task.
export default function ComicsEditorPage({ memesApi }: Props) {
  void memesApi
  const { id } = useParams()
  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Comics</h1>
      <p>{id}</p>
    </div>
  )
}
