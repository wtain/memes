import type { AxisName, QueryProfile } from "./types"
import { SITUATION_LIMIT } from "./types"

/** Add a user-chosen value; the same object comes back when nothing changes (duplicate or axis full). */
export function addValue(profile: QueryProfile, axis: AxisName, key: string, maxValues: number): QueryProfile {
  const current = profile[axis]
  if (current.some((value) => value.key === key) || current.length >= maxValues) return profile
  return { ...profile, [axis]: [...current, { key, confidence: 1, source: "human" as const }] }
}

export function removeValue(profile: QueryProfile, axis: AxisName, key: string): QueryProfile {
  if (!profile[axis].some((value) => value.key === key)) return profile
  return { ...profile, [axis]: profile[axis].filter((value) => value.key !== key) }
}

export function setSituation(profile: QueryProfile, text: string): QueryProfile {
  // Only the empty string means "no situation". Nothing is trimmed here: a typed leading space must survive (the server strips).
  const next = text === "" ? null : text.slice(0, SITUATION_LIMIT)
  if ((profile.situation ?? null) === next) return profile
  return { ...profile, situation: next, situationConfidence: null }
}

/** An edited profile is a reading the user vouches for: it is no longer degraded. */
export function toEditedProfile(profile: QueryProfile): QueryProfile {
  return { ...profile, degraded: false }
}

export function hasAnyValue(profile: QueryProfile): boolean {
  return Boolean(profile.situation?.trim()) || profile.emotion.length + profile.stance.length + profile.topic.length + profile.tone.length > 0
}
