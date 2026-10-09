import { useEffect, useRef, useState } from "react"

type Props = {
  onConfirm: () => Promise<boolean>
}

type Phase = "idle" | "confirming" | "working" | "done" | "failed"

export default function ForgetTextButton({ onConfirm }: Props) {
  const [phase, setPhase] = useState<Phase>("idle")
  const forgetRef = useRef<HTMLButtonElement>(null)
  const refocus = useRef(false)

  // After the confirm step closes, focus returns to the button that opened it.
  useEffect(() => {
    if (phase === "confirming" || phase === "working") return
    if (!refocus.current) return
    refocus.current = false
    forgetRef.current?.focus()
  }, [phase])

  async function confirm() {
    setPhase("working")
    const ok = await onConfirm()
    refocus.current = true
    setPhase(ok ? "done" : "failed")
  }

  function cancel() {
    refocus.current = true
    setPhase("idle")
  }

  const confirming = phase === "confirming" || phase === "working"

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {confirming ? (
        <>
          <span>This clears the stored text of this message; its ratings stay. Rating again stores the text again.</span>
          <button type="button" disabled={phase === "working"} onClick={confirm} className="min-h-11 rounded border px-3">Confirm</button>
          <button type="button" disabled={phase === "working"} onClick={cancel} className="min-h-11 rounded border px-3">Cancel</button>
        </>
      ) : (
        <button ref={forgetRef} type="button" onClick={() => setPhase("confirming")} className="min-h-11 text-blue-600 hover:underline">
          Forget this message's text
        </button>
      )}
      <span role="status" className="sr-only">{phase === "done" ? "Text forgotten" : ""}</span>
      {phase === "done" && <span aria-hidden="true">Text forgotten</span>}
      {phase === "failed" && <span role="alert" className="text-red-700">Couldn't forget the text</span>}
    </div>
  )
}
