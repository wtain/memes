import { useEffect, useState } from "react"
import { useParams } from "react-router-dom"
import { MemesApi } from "../api/MemesApi"
import { MemeDetails } from "../components/MemeDetails"
import { Meme } from "../types/generated/all"

type Props = {
  memesApi: MemesApi
}

export default function MemePage({ memesApi }: Props) {
  const { id } = useParams<{ id: string }>()
  const [meme, setMeme] = useState<Meme | null>(null)

  useEffect(() => {
    if (!id) return

    memesApi.getMeme(id).then(setMeme)
  }, [id])

  if (!meme) {
    return <div>Loading...</div>
  }

  return (
    <div className="max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold mb-4">
        Meme {meme.id}
      </h1>

      <MemeDetails meme={meme} memesApi={memesApi} />
    </div>
  )
}