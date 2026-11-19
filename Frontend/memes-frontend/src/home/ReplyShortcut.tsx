import { Link } from "react-router-dom"
import { useFeature } from "../features/useFeature"
import { Card, buttonClasses } from "../ui"

export function ReplyShortcut() {
  const visible = useFeature("reply")
  if (!visible) return null
  return (
    <Card padded className="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="text-base font-semibold text-fg">Reply with a meme</p>
        <p className="text-sm text-fg-muted">Paste a message you want to answer and get matching memes.</p>
      </div>
      <Link to="/reply" className={buttonClasses({ variant: "secondary", className: "w-full sm:w-auto" })}>
        Open Reply
      </Link>
    </Card>
  )
}
