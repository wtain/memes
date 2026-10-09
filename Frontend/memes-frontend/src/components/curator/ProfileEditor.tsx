import { useEffect, useId, useRef, useState } from "react"
import type { MemesApi } from "../../api/MemesApi"
import { useProfileEditor } from "../../curator/useProfileEditor"
import type { EditorEvent } from "../../curator/useProfileEditor"
import { useUnsavedGuard } from "../../curator/useUnsavedGuard"
import { axisSpec, labelFor, maxValuesFor, optionsFor } from "../../reply/taxonomy"
import { useTaxonomy } from "../../reply/useTaxonomy"
import { AXES, AXIS_LABELS, SITUATION_LIMIT } from "../../reply/types"
import ChipGroup from "../reply/ChipGroup"

type Props = {
  memesApi: MemesApi
  imageId: string
}

type FocusTarget = "save" | "confirm" | "reset" | "discard" | "confirmReset" | "cancel" | "stay" | "heading"

const USE_WHEN_LIMIT = 300

function announcement(event: EditorEvent | null): string {
  if (event === null) return ""
  switch (event.kind) {
    case "saved":
      return event.vector === "pending"
        ? "Saved. The search vector is pending and will be refreshed by the next embeddings run."
        : "Saved"
    case "confirmed": return "Confirmed as correct"
    case "reset": return "Reset. The next batch run re-extracts this profile."
    case "failed": return ""
  }
}

const FAILURES = {
  save: "Couldn't save the profile",
  confirm: "Couldn't confirm the profile",
  reset: "Couldn't reset the profile",
} as const

export default function ProfileEditor({ memesApi, imageId }: Props) {
  const editor = useProfileEditor(memesApi, imageId)
  const { profile, draft, dirty, busy, event } = editor
  const taxonomy = useTaxonomy(memesApi, profile?.currentTaxonomyVersion ?? null)
  const blocker = useUnsavedGuard(dirty)
  const [confirmingReset, setConfirmingReset] = useState(false)

  const headingId = useId()
  const situationId = useId()
  const useWhenId = useId()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const saveRef = useRef<HTMLButtonElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const resetRef = useRef<HTMLButtonElement>(null)
  const discardRef = useRef<HTMLButtonElement>(null)
  const confirmResetRef = useRef<HTMLButtonElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const stayRef = useRef<HTMLButtonElement>(null)
  const pendingFocus = useRef<FocusTarget | null>(null)

  // Focus returns to the button that was used once the work is done, or to the heading when that button
  // is gone (or disabled). The request is made in a handler and consumed here, after the state settled.
  useEffect(() => {
    const target = pendingFocus.current
    if (target === null || busy !== null) return
    pendingFocus.current = null
    const buttons: Record<FocusTarget, HTMLElement | null> = {
      save: saveRef.current, confirm: confirmRef.current, reset: resetRef.current, discard: discardRef.current,
      confirmReset: confirmResetRef.current, cancel: cancelRef.current, stay: stayRef.current, heading: headingRef.current,
    }
    const element = buttons[target]
    const usable = element !== null && !(element instanceof HTMLButtonElement && element.disabled)
    const focusTarget = usable ? element : headingRef.current
    focusTarget?.focus()
  })

  // The unsaved-changes bar takes focus when it appears.
  const blocked = blocker.state === "blocked"
  useEffect(() => {
    if (blocked) stayRef.current?.focus()
  }, [blocked])

  if (editor.status === "loading") {
    return (
      <section aria-labelledby={headingId} className="mt-6 rounded-xl bg-white p-4 shadow-sm">
        <h2 id={headingId} className="text-lg font-semibold">Profile</h2>
        <p role="status" className="text-sm text-gray-500">Loading the profile...</p>
      </section>
    )
  }

  if (editor.status === "error" || profile === null) {
    return (
      <section aria-labelledby={headingId} className="mt-6 rounded-xl bg-white p-4 shadow-sm">
        <h2 id={headingId} className="text-lg font-semibold">Profile</h2>
        <p role="alert" className="text-red-700">Couldn't load the profile</p>
      </section>
    )
  }

  function request(target: FocusTarget, action: () => void) {
    pendingFocus.current = target
    action()
  }

  const exists = profile.exists
  const badge = !exists ? "No profile yet" : profile.humanVerified ? "Human-verified" : "Model profile"
  const showConfirm = exists && !dirty && (!profile.humanVerified || profile.needsReview)
  const working = busy !== null
  const failure = event?.kind === "failed" ? FAILURES[event.action] : null
  const message = dirty ? "" : announcement(event)

  return (
    <section aria-labelledby={headingId} className="mt-6 rounded-xl bg-white p-4 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <h2 id={headingId} ref={headingRef} tabIndex={-1} className="text-lg font-semibold">Profile</h2>
        <span className="rounded bg-gray-100 px-2 py-0.5 text-sm">{badge}</span>
        {dirty && <span className="rounded bg-yellow-100 px-2 py-0.5 text-sm">Unsaved changes</span>}
      </div>

      {profile.needsReview && (
        <p className="mb-3 rounded bg-yellow-50 px-3 py-2 text-sm">
          Needs review: The vocabulary changed since this profile was verified. Check the values, then confirm or edit them.
        </p>
      )}

      <label htmlFor={situationId} className="mb-1 block text-sm font-medium text-gray-700">Situation</label>
      <textarea
        id={situationId}
        rows={2}
        value={draft.situation}
        maxLength={axisSpec(taxonomy, "situation")?.maxChars ?? SITUATION_LIMIT}
        onChange={(e) => editor.setSituation(e.target.value)}
        className="mb-4 w-full rounded-md border px-3 py-2"
      />

      <label htmlFor={useWhenId} className="mb-1 block text-sm font-medium text-gray-700">Use when</label>
      <textarea
        id={useWhenId}
        rows={2}
        value={draft.useWhen}
        maxLength={USE_WHEN_LIMIT}
        onChange={(e) => editor.setUseWhen(e.target.value)}
        className="mb-4 w-full rounded-md border px-3 py-2"
      />

      {AXES.map((axis) => {
        const saved = profile.values[axis]
        return (
          <ChipGroup
            key={axis}
            axis={axis}
            label={AXIS_LABELS[axis]}
            values={draft.values[axis].map((key) => saved.find((item) => item.key === key) ?? { key, confidence: 1, source: "human" as const })}
            labelOf={(key) => labelFor(taxonomy, axis, key)}
            options={optionsFor(taxonomy, axis)}
            maxValues={maxValuesFor(taxonomy, axis)}
            pickerDisabled={taxonomy === null}
            onAdd={(key) => editor.add(axis, key)}
            onRemove={(key) => editor.remove(axis, key)}
          />
        )
      })}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          ref={saveRef}
          type="button"
          disabled={!dirty || working}
          onClick={() => request("save", () => { void editor.save() })}
          className="min-h-11 rounded-md bg-blue-600 px-4 text-white disabled:opacity-40">
          Save
        </button>
        {dirty && (
          <button
            ref={discardRef}
            type="button"
            disabled={working}
            onClick={() => request("heading", () => editor.discard())}
            className="min-h-11 rounded-md border px-4 disabled:opacity-40">
            Discard changes
          </button>
        )}
        {showConfirm && (
          <button
            ref={confirmRef}
            type="button"
            disabled={working}
            onClick={() => request("confirm", () => { void editor.confirm() })}
            className="min-h-11 rounded-md border px-4 disabled:opacity-40">
            Confirm as correct
          </button>
        )}
        {exists && !confirmingReset && (
          <button
            ref={resetRef}
            type="button"
            disabled={working}
            onClick={() => request("confirmReset", () => setConfirmingReset(true))}
            className="min-h-11 rounded-md px-4 text-blue-600 hover:underline disabled:opacity-40">
            Reset to model
          </button>
        )}
        {exists && confirmingReset && (
          <>
            <span className="text-sm">This clears the curated profile; the next batch run extracts a new one.</span>
            <button
              ref={confirmResetRef}
              type="button"
              disabled={working}
              onClick={() => request("reset", () => { setConfirmingReset(false); void editor.reset() })}
              className="min-h-11 rounded-md border px-4 disabled:opacity-40">
              Confirm reset
            </button>
            <button
              ref={cancelRef}
              type="button"
              disabled={working}
              onClick={() => request("reset", () => setConfirmingReset(false))}
              className="min-h-11 rounded-md border px-4 disabled:opacity-40">
              Cancel
            </button>
          </>
        )}
      </div>

      <p role="status" className="sr-only">{message}</p>
      {exists && profile.vector === "pending" && (
        <p className="mt-2 text-sm text-gray-600">The search vector is pending and will be refreshed by the next embeddings run.</p>
      )}
      {failure !== null && <p role="alert" className="mt-2 text-red-700">{failure}</p>}

      {blocked && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded bg-yellow-50 px-3 py-2">
          <span>You have unsaved changes.</span>
          <button type="button" onClick={() => blocker.proceed()} className="min-h-11 rounded-md border px-4">Leave without saving</button>
          <button ref={stayRef} type="button" onClick={() => request("heading", () => blocker.reset())} className="min-h-11 rounded-md border px-4">Stay</button>
        </div>
      )}
    </section>
  )
}
