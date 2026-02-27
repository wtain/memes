import { Meme } from "../types/meme"
import { useState } from "react"
import { TagList } from "./TagList"

type Props = {
  meme: Meme
  baseUrl: string
  onClick?: () => void
}

export default function MemeCard({ meme, baseUrl, onClick }: Props) {
  const [showText, setShowText] = useState(false)

  return (
    <div
      className="relative overflow-hidden rounded-xl border bg-white shadow-sm hover:shadow-md transition"
      onClick={onClick}
      onMouseEnter={() => setShowText(true)}
      onMouseLeave={() => setShowText(false)}
    >
      {/* Image */}
      <img
        src={baseUrl + meme.imageUrl}
        alt={meme.id}
        className="aspect-square w-full object-cover"
        loading="lazy"
      />

      <TagList tags={meme.tags} />

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
  )
}
