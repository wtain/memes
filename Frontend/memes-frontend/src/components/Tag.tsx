import { Badge } from "../ui"

type Props = {
  label: string
  title?: string
  className?: string
}

export function Tag({ label, title, className }: Props) {
  return (
    <Badge title={title} className={className}>
      #{label}
    </Badge>
  )
}
