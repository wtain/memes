import { Meme } from "../types/meme"
import { useState } from "react"
import { TagList } from "./TagList"

type Props = {
  meme: Meme
  baseUrl: string
}

export default function MemeCard({ meme, baseUrl }: Props) {
  const [showText, setShowText] = useState(false)

  return (
    <div
      className="relative overflow-hidden rounded-xl border bg-white shadow-sm hover:shadow-md transition"
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

      {/* <div className="space-y-1 mb-3">
        {meme.text.map((line, i) => (
          <div key={i} className="text-sm">
            {line}
          </div>
        ))}
      </div> */}

      {/* Tags overlay */}
      {/* <div className="absolute bottom-2 left-2 right-2 flex flex-wrap gap-1">
        {meme.tags.slice(0, 4).map((tag) => (
          <span
            key={tag.name}
            className="rounded-full bg-black/70 px-2 py-0.5 text-xs text-white"
          >
            {tag.name}
          </span>
        ))}

        {meme.tags.length > 4 && (
          <span className="rounded-full bg-black/50 px-2 py-0.5 text-xs text-white">
            +{meme.tags.length - 4}
          </span>
        )}
      </div> */}

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
