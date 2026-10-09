import { useState } from "react"

type Props = {
  onConfirm: () => Promise<boolean>
}

type Phase = "idle" | "confirming" | "working" | "done" | "failed"

export default function ForgetTextButton({ onConfirm }: Props) {
  const [phase, setPhase] = useState<Phase>("idle")

  async function confirm() {
    setPhase("working")
    setPhase((await onConfirm()) ? "done" : "failed")
  }

  if (phase === "confirming" || phase === "working") {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span>This clears the stored text of this message; its ratings stay. Rating again stores the text again.</span>
        <button type="button" disabled={phase === "working"} onClick={confirm} className="min-h-11 rounded border px-3">Confirm</button>
        <button type="button" onClick={() => setPhase("idle")} className="min-h-11 rounded border px-3">Cancel</button>
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <button type="button" onClick={() => setPhase("confirming")} className="min-h-11 text-blue-600 hover:underline">
        Forget this message's text
      </button>
      <span role="status">{phase === "done" ? "Text forgotten" : ""}</span>
      {phase === "failed" && <span role="alert" className="text-red-700">Couldn't forget the text</span>}
    </div>
  )
}
