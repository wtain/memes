import { useState } from "react"
import { TagList } from "./TagList"
import { MemesApi } from "../api/MemesApi"
import { Meme } from "../types/generated/all"

type Props = {
  meme: Meme
  memesApi: MemesApi
  onClick?: () => void
}

export default function MemeCard({ meme, memesApi, onClick }: Props) {
  const [showText, setShowText] = useState(false)

  return (
    <div
      className="relative overflow-hidden rounded-xl border bg-white shadow-sm hover:shadow-md transition"
    >
      <div className="relative"
        onClick={onClick}
        onMouseEnter={() => setShowText(true)}
        onMouseLeave={() => setShowText(false)}
      >
        {/* Image */}
        <img
          src={memesApi.getImageUrl(meme)}
          alt={meme.id}
          className="aspect-square w-full object-cover"
          loading="lazy"
        />

        {/* OCR text overlay */}
        {showText && meme.text.length > 0 && (
          <div className="absolute inset-0 bg-black/70 p-3 text-sm text-white overflow-y-auto">
            <div className="font-semibold mb-2">OCR</div>
            <ul className="space-y-1">
              {meme.text.map((line, i) => (
                <li key={i} className="opacity-90">
                  {line}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <TagList tags={meme.tags} />
    </div>
  )
}
