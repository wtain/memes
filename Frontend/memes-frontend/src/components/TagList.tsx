import { useState } from "react"
import type { MemeTag } from "../types/generated/all"
import { Badge } from "../ui"
import { Tag } from "./Tag"

type Props = {
  tags: MemeTag[]
  variant?: "full" | "compact"
}

const VISIBLE_TAG_LIMIT = 3
const COMPACT_TAG_LIMIT = 2

export function TagList({ tags, variant = "full" }: Props) {
  const [expanded, setExpanded] = useState(false)

  if (tags === null) {
    return(<></>)
  }

  const qualifyingTags = tags.filter(tag => tag.score! > 0.3)

  if (variant === "compact") {
    const shown = qualifyingTags.slice(0, COMPACT_TAG_LIMIT)
    const hidden = qualifyingTags.slice(COMPACT_TAG_LIMIT)
    return (
      <div className="flex flex-nowrap items-center gap-1 overflow-hidden">
        {shown.map(tag => (
          <Tag
            key={`${tag.category}:${tag.name}:${tag.source}`}
            label={tag.name}
            title={`${tag.name} (${tag.source})`}
            className="max-w-24 truncate"
          />
        ))}
        {hidden.length > 0 && (
          <Badge className="shrink-0" title={hidden.map(tag => tag.name).join(", ")}>
            +{hidden.length}
          </Badge>
        )}
      </div>
    )
  }

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
