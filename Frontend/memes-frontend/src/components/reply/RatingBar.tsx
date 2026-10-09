import type { Grade } from "../../reply/types"
import { GRADE_CHOICES } from "../../reply/types"

export type RatingBarEvent = "saved" | "cleared" | "failed" | null

type Props = {
  grade: Grade | null
  event: RatingBarEvent
  onRate: (grade: Grade | null) => void
}

export default function RatingBar({ grade, event, onRate }: Props) {
  const active = GRADE_CHOICES.find((choice) => choice.grade === grade)
  const announcement = event === "saved" && active ? `Saved: ${active.label}` : event === "cleared" ? "Rating cleared" : ""

  return (
    <div role="group" aria-label="Rate this result" className="mt-2 flex flex-wrap items-center gap-2 text-sm">
      {GRADE_CHOICES.map((choice) => (
        <button
          key={choice.grade}
          type="button"
          aria-pressed={grade === choice.grade}
          onClick={() => onRate(grade === choice.grade ? null : choice.grade)}
          className={`min-h-11 rounded-md border px-3 ${grade === choice.grade ? "border-blue-600 bg-blue-600 font-medium text-white" : "bg-white"}`}
        >
          {choice.label}
        </button>
      ))}
      <span role="status" className="sr-only">{announcement}</span>
      {event === "failed" && <span role="alert" className="text-red-700">Couldn't save the rating</span>}
    </div>
  )
}
