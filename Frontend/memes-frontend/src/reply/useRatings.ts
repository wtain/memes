import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { Grade, RatingContext } from "./types"

export type RatingEvent = { imageId: string; kind: "saved" | "cleared" | "failed" }

type Loaded = { hash: string | null; grades: Record<string, Grade> }

export function useRatings(memesApi: MemesApi, queryHash: string | null) {
  const [loaded, setLoaded] = useState<Loaded>({ hash: null, grades: {} })
  const [lastEvent, setLastEvent] = useState<RatingEvent | null>(null)
  // Grades of the message on screen; a different message starts empty (derived, no setState in an effect).
  const grades = loaded.hash === queryHash ? loaded.grades : {}

  const confirmed = useRef<Record<string, Grade | null>>({})
  const chains = useRef(new Map<string, Promise<void>>())
  const hashRef = useRef(queryHash)

  useEffect(() => {
    hashRef.current = queryHash
    confirmed.current = {}
    chains.current = new Map()
    if (!queryHash) return
    let cancelled = false
    memesApi.getRatings(queryHash).then(
      (response) => {
        if (cancelled) return
        const fetched: Record<string, Grade> = {}
        for (const item of response.ratings) fetched[item.imageId] = item.grade as Grade
        for (const [imageId, grade] of Object.entries(fetched)) confirmed.current[imageId] = grade
        // grades the user set while the load was in flight win over the fetched ones
        setLoaded((previous) => ({
          hash: queryHash, grades: { ...fetched, ...(previous.hash === queryHash ? previous.grades : {}) },
        }))
      },
      () => { /* a failed load is silent: the bar starts empty */ },
    )
    return () => { cancelled = true }
  }, [memesApi, queryHash])

  function setGrade(hash: string, imageId: string, grade: Grade | null) {
    setLoaded((previous) => {
      const base = previous.hash === hash ? previous.grades : {}
      const next = { ...base }
      if (grade === null) delete next[imageId]
      else next[imageId] = grade
      return { hash, grades: next }
    })
  }

  function rate(context: RatingContext, imageId: string, grade: Grade | null) {
    const hash = queryHash
    if (!hash) return
    setGrade(hash, imageId, grade)
    const run = async () => {
      if (hashRef.current !== hash) return                      // the message changed while this click waited
      try {
        if (grade === null) await memesApi.deleteRating(hash, imageId)
        else await memesApi.putRating({ ...context, imageId, grade })
        confirmed.current[imageId] = grade
        if (hashRef.current === hash) {
          setGrade(hash, imageId, grade)
          setLastEvent({ imageId, kind: grade === null ? "cleared" : "saved" })
        }
      } catch {
        if (hashRef.current === hash) {
          setGrade(hash, imageId, confirmed.current[imageId] ?? null)
          setLastEvent({ imageId, kind: "failed" })
        }
      }
    }
    const previous = chains.current.get(imageId) ?? Promise.resolve()
    chains.current.set(imageId, previous.then(run))
  }

  async function forgetText(): Promise<boolean> {
    if (!queryHash) return false
    try {
      await memesApi.forgetRatingText(queryHash)
      return true
    } catch {
      return false
    }
  }

  return { grades, hasRatings: Object.keys(grades).length > 0, lastEvent, rate, forgetText }
}
