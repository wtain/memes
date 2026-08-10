import { useState } from "react"
import type { MemeTag } from "../types/generated/all"
import { Tag } from "./Tag"

type Props = {
  tags: MemeTag[]
}

const VISIBLE_TAG_LIMIT = 3

export function TagList({ tags }: Props) {
  const [expanded, setExpanded] = useState(false)

  if (tags === null) {
    return(<></>)
  }

  const qualifyingTags = tags.filter(tag => tag.score! > 0.3)
  const hasMore = qualifyingTags.length > VISIBLE_TAG_LIMIT
  const visibleTags = expanded ? qualifyingTags : qualifyingTags.slice(0, VISIBLE_TAG_LIMIT)
  const hiddenCount = qualifyingTags.length - VISIBLE_TAG_LIMIT

  return (
    <div className="flex flex-wrap gap-2 items-center">
      {visibleTags.map(tag => (
        <Tag key={`${tag.category}:${tag.name}:${tag.source}`} label={`${tag.name} (${tag.source})`} />
      ))}
      {hasMore && (
        <button
          type="button"
          className="text-xs px-2 py-1 bg-gray-200 rounded-full"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "show less" : `+${hiddenCount} more`}
        </button>
      )}
    </div>
  )
}
