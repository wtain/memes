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
  const [isExcluded, setIsExcluded] = useState(meme.excluded ?? false)

  return (
    <div className={`relative overflow-hidden rounded-xl bg-white shadow-sm hover:shadow-md transition border-2 ${isExcluded ? "border-red-500" : "border-black"}`}>
      <div
        className="relative"
        onClick={onClick}
        onMouseEnter={() => setShowText(true)}
        onMouseLeave={() => setShowText(false)}
      >
        <img
          src={memesApi.getImageUrl(meme)}
          alt={meme.id}
          className={`w-full object-cover ${variant === "square" ? "aspect-square" : ""}`}  // 👈
          loading="lazy"
        />

        {showText && (meme.text ?? []).length > 0 && (
          <div className="absolute inset-0 bg-black/70 p-3 text-sm text-white overflow-y-auto">
            <div className="font-semibold mb-2">OCR</div>
            <ul className="space-y-1">
              {(meme.text ?? []).map((line, i) => (
                <li key={i} className="opacity-90">{line}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
      {meme.clusterId !== undefined && (
        <div className="px-4 py-1 text-xs text-gray-500 border-b">Cluster {meme.clusterId}</div>
      )}
      <div>
        <input type="checkbox" className="ml-4 mr-2" checked={isExcluded} onChange={(e) => {
          e.preventDefault();
          if (isExcluded) {
            memesApi.unmarkImageIsExcluded(meme.id).then(() => setIsExcluded(false));
          }
          else {
            memesApi.markImageIsExcluded(meme.id).then(() => setIsExcluded(true));
          }
          
        }} />
        Excluded
      </div>
      <TagList tags={meme.tags ?? []} />
    </div>
  )
}