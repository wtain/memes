import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { AxisName } from "../reply/types"
import { addKey, removeKey, sameDraft, toDraft, toSaveRequest } from "./draft"
import type { Draft } from "./draft"
import type { CuratedProfile } from "./types"

export type Busy = "save" | "confirm" | "reset"

export type EditorEvent =
  | { kind: "saved"; vector: "fresh" | "pending" | "none" }
  | { kind: "confirmed" }
  | { kind: "reset" }
  | { kind: "failed"; action: Busy }

type Loaded = { id: string; profile: CuratedProfile | null; failed: boolean }

export function useProfileEditor(memesApi: MemesApi, imageId: string) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [edits, setEdits] = useState<{ id: string; draft: Draft } | null>(null)
  const [busyState, setBusyState] = useState<{ id: string; action: Busy } | null>(null)
  const [eventState, setEventState] = useState<{ id: string; event: EditorEvent } | null>(null)

  // The image the hook currently serves, and whether an action is in flight (set in handlers and effects only).
  const currentId = useRef(imageId)
  const inFlight = useRef(false)

  const current = loaded !== null && loaded.id === imageId ? loaded : null
  const profile = current?.profile ?? null
  const base = toDraft(profile)
  const draft = edits !== null && edits.id === imageId ? edits.draft : base
  const dirty = edits !== null && edits.id === imageId && !sameDraft(edits.draft, base)
  const status = current === null ? "loading" : current.failed ? "error" : "ready"
  const busy = busyState !== null && busyState.id === imageId ? busyState.action : null
  const event = eventState !== null && eventState.id === imageId ? eventState.event : null

  useEffect(() => {
    currentId.current = imageId
    inFlight.current = false
    let cancelled = false
    memesApi.getCuratedProfile(imageId).then(
      (next) => {
        if (cancelled) return
        setLoaded({ id: imageId, profile: next, failed: false })
      },
      () => {
        if (cancelled) return
        setLoaded({ id: imageId, profile: null, failed: true })
      },
    )
    return () => { cancelled = true }
  }, [memesApi, imageId])

  function edit(change: (previous: Draft) => Draft) {
    setEdits({ id: imageId, draft: change(draft) })
  }

  async function run(action: Busy, call: () => Promise<CuratedProfile>) {
    if (inFlight.current || current === null || current.profile === null) return
    const id = imageId
    inFlight.current = true
    setBusyState({ id, action })
    try {
      const next = await call()
      if (currentId.current !== id) return
      setLoaded({ id, profile: next, failed: false })
      setEdits(null)
      setEventState({ id, event: action === "save"
        ? { kind: "saved", vector: next.vector }
        : action === "confirm" ? { kind: "confirmed" } : { kind: "reset" } })
    } catch {
      if (currentId.current !== id) return
      setEventState({ id, event: { kind: "failed", action } })
    } finally {
      if (currentId.current === id) {
        inFlight.current = false
        setBusyState((previous) => (previous !== null && previous.id === id ? null : previous))
      }
    }
  }

  function save() {
    return run("save", () => memesApi.saveCuratedProfile(imageId, toSaveRequest(draft)))
  }

  function confirm() {
    return run("confirm", () => memesApi.confirmCuratedProfile(imageId))
  }

  function reset() {
    const version = profile?.currentTaxonomyVersion ?? 0
    return run("reset", async () => {
      await memesApi.resetCuratedProfile(imageId)
      return emptyProfile(imageId, version)
    })
  }

  return {
    status, profile, draft, dirty, busy, event,
    setSituation: (text: string) => edit((previous) => ({ ...previous, situation: text })),
    setUseWhen: (text: string) => edit((previous) => ({ ...previous, useWhen: text })),
    add: (axis: AxisName, key: string) => edit((previous) => addKey(previous, axis, key)),
    remove: (axis: AxisName, key: string) => edit((previous) => removeKey(previous, axis, key)),
    discard: () => setEdits(null),
    save, confirm, reset,
  }
}

function emptyProfile(imageId: string, currentTaxonomyVersion: number): CuratedProfile {
  return {
    imageId, exists: false, humanVerified: false, needsReview: false, taxonomyVersion: null,
    currentTaxonomyVersion, situation: null, situationConfidence: null, useWhen: null,
    values: { emotion: [], stance: [], topic: [], tone: [] },
    vector: "none", extractedAt: null, updatedAt: null,
  }
}
