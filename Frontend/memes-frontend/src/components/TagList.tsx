import { MemeTag } from "../types/generated/all"
import { Tag } from "./Tag"

type Props = {
  tags: MemeTag[]
}

export function TagList({ tags }: Props) {
    if (tags === null) {
        return(<></>)
    }
  return (
    <div className="flex flex-wrap gap-2">
      {tags.filter(tag => tag.score! > 0.3)
           .map(tag => (
        <Tag key={`${tag.category}:${tag.name}:${tag.source}`} label={`${tag.name} (${tag.source})`} />
      ))}
    </div>
  )
}
