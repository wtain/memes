import { MemeTag } from "../types/meme"
import { Tag } from "./Tag"

type Props = {
  tags: MemeTag[]
}

export function TagList({ tags }: Props) {
    if (tags === null) {
        return(<></>)
    }
    // console.log(tags)
  return (
    <div className="flex flex-wrap gap-2">
      {tags.filter(tag => tag.score! > 0.3)
           .map(tag => (
        <Tag key={tag.category} label={`${tag.name} (${tag.score})`} />
      ))}
    </div>
  )
}
