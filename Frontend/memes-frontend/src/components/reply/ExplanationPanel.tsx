import type { MemeFromTextResult } from "../../reply/types"
import type { AxisExplanation } from "../../types/generated/all"

type Props = {
  result: MemeFromTextResult
  labelOf: (axis: string, key: string) => string
}

const AXIS_NAMES: Record<string, string> = {
  situation: "Situation", emotion: "Emotion", stance: "Stance", topic: "Topic", tone: "Tone", lexical: "Word match",
}

function describe(axis: AxisExplanation, labelOf: Props["labelOf"]): string {
  if (axis.neutral) return "no value on this axis (neutral)"
  if (axis.axis === "situation") return "similar situation"
  if (axis.axis === "lexical") return "words from your message appear in this meme"
  if (axis.matched.length === 0) return "no matching value"
  return axis.matched
    .map((pair) => {
      const meme = labelOf(axis.axis, pair.memeKey)
      return pair.queryKey === pair.memeKey ? meme : `${meme} (close to your ${labelOf(axis.axis, pair.queryKey)})`
    })
    .join(", ")
}

export default function ExplanationPanel({ result, labelOf }: Props) {
  const { axes, situationText, useWhen } = result.explanation
  return (
    <div className="mt-2 space-y-2 rounded-md bg-gray-50 p-3 text-sm">
      <ul className="space-y-1">
        {axes.map((axis) => {
          const percent = Math.round(axis.score * 100)
          return (
            <li key={axis.axis}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{AXIS_NAMES[axis.axis] ?? axis.axis}</span>
                <span>{percent}%</span>
              </div>
              <div className="h-1.5 rounded bg-gray-200" aria-hidden="true">
                <div className="h-1.5 rounded bg-blue-500" style={{ width: `${percent}%` }} />
              </div>
              <div className="text-gray-600">{describe(axis, labelOf)}</div>
            </li>
          )
        })}
      </ul>
      {situationText && <p className="text-gray-800">{situationText}</p>}
      {useWhen && <p className="text-gray-600">{useWhen}</p>}
    </div>
  )
}
