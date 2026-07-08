import { useState } from "react"
import { TagList } from "./TagList"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"

type Props = {
  meme: Meme
  memesApi: MemesApi
  onClick?: () => void
  variant?: "square" | "full"  // 👈
}

export default function MemeCard({ meme, memesApi, onClick, variant = "square" }: Props) {
  const [showText, setShowText] = useState(false)
  const [isFlagged, setIsFlagged] = useState(meme.flagged ?? false)

  return (
    <div className={`relative overflow-hidden rounded-xl bg-white shadow-sm hover:shadow-md transition border-2 ${isFlagged ? "border-red-500" : "border-black"}`}>
      <div className="relative">
        <img
          src={memesApi.getImageUrl(meme)}
          alt={meme.id}
          className={`w-full object-cover ${variant === "square" ? "aspect-square" : ""}`}  // 👈
          loading="lazy"
          onClick={showText ? undefined : onClick}
        />

        {(meme.text ?? []).length > 0 && (
          <button
            className="absolute bottom-1 right-1 text-xs bg-black/50 text-white rounded px-1.5 py-0.5 hover:bg-black/70"
            onClick={e => { e.stopPropagation(); setShowText(true) }}
          >
            OCR
          </button>
        )}

        {showText && (
          <div
            className="absolute inset-0 bg-black/70 p-3 text-sm text-white overflow-y-auto cursor-pointer"
            onClick={e => { e.stopPropagation(); setShowText(false) }}
          >
            <div className="font-semibold mb-2">OCR <span className="text-xs font-normal opacity-60">(click to close)</span></div>
            <ul className="space-y-1">
              {(meme.text ?? []).map((line, i) => (
                <li key={i} className="opacity-90">{line}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      {meme.clusterId != null && (
        <div className="px-4 py-1 text-xs text-gray-500 border-b">Cluster {meme.clusterId}</div>
      )}
      <div>
        <input type="checkbox" className="ml-4 mr-2" checked={isFlagged} onChange={(e) => {
          e.preventDefault();
          if (isFlagged) {
            memesApi.unmarkImageIsFlagged(meme.id).then(() => setIsFlagged(false));
          }
          else {
            memesApi.markImageIsFlagged(meme.id).then(() => setIsFlagged(true));
          }
          
        }} />
        Flagged
      </div>
      <TagList tags={meme.tags ?? []} />
    </div>
  )
}