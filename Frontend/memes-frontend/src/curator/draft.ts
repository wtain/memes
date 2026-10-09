import type { AxisName } from "../reply/types"
import { AXES } from "../reply/types"
import type { CuratedProfile, CuratedProfileSaveRequest } from "./types"

export type Draft = { situation: string; useWhen: string; values: Record<AxisName, string[]> }

export function emptyDraft(): Draft {
  return { situation: "", useWhen: "", values: { emotion: [], stance: [], topic: [], tone: [] } }
}

export function toDraft(profile: CuratedProfile | null): Draft {
  if (profile === null || !profile.exists) return emptyDraft()
  const draft = emptyDraft()
  draft.situation = profile.situation ?? ""
  draft.useWhen = profile.useWhen ?? ""
  for (const axis of AXES) draft.values[axis] = profile.values[axis].map((value) => value.key)
  return draft
}

function sameSet(a: string[], b: string[]): boolean {
  const left = new Set(a)
  const right = new Set(b)
  return left.size === right.size && [...left].every((key) => right.has(key))
}

export function sameDraft(a: Draft, b: Draft): boolean {
  return a.situation.trim() === b.situation.trim()
    && a.useWhen.trim() === b.useWhen.trim()
    && AXES.every((axis) => sameSet(a.values[axis], b.values[axis]))
}

export function toSaveRequest(draft: Draft): CuratedProfileSaveRequest {
  return {
    situation: draft.situation.trim() || null,
    useWhen: draft.useWhen.trim() || null,
    values: {
      emotion: [...draft.values.emotion], stance: [...draft.values.stance],
      topic: [...draft.values.topic], tone: [...draft.values.tone],
    },
  }
}

export function addKey(draft: Draft, axis: AxisName, key: string): Draft {
  if (draft.values[axis].includes(key)) return { ...draft, values: { ...draft.values } }
  return { ...draft, values: { ...draft.values, [axis]: [...draft.values[axis], key] } }
}

export function removeKey(draft: Draft, axis: AxisName, key: string): Draft {
  return { ...draft, values: { ...draft.values, [axis]: draft.values[axis].filter((existing) => existing !== key) } }
}
