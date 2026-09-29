import { useEffect, useState } from "react"
import type { StatisticsResponse } from "../types/generated/all"
import { lerpStats } from "./lerpStats"

export const TWEEN_DURATION_MS = 500

type Animation = { from: StatisticsResponse; to: StatisticsResponse }
type TweenState = {
  target: StatisticsResponse | null
  shown: StatisticsResponse | null
  animation: Animation | null
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
}

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}

export function useTweenedStats(
  target: StatisticsResponse | null,
  durationMs = TWEEN_DURATION_MS,
): StatisticsResponse | null {
  const [state, setState] = useState<TweenState>({ target, shown: target, animation: null })

  // Derive state from props during render: React re-renders immediately with the
  // adjusted state, so the first value handed out after a change is the OLD stats.
  if (target !== state.target) {
    const animate = target !== null
      && state.shown !== null
      && durationMs > 0
      && !prefersReducedMotion()
    setState({
      target,
      shown: animate ? state.shown : target,
      animation: animate ? { from: state.shown!, to: target } : null,
    })
  }

  const animation = state.animation
  useEffect(() => {
    if (!animation) return
    let frame = 0
    let startedAt: number | null = null

    const step = (now: number) => {
      if (startedAt === null) startedAt = now
      const progress = Math.min((now - startedAt) / durationMs, 1)
      if (progress >= 1) {
        setState((s) => (s.animation === animation ? { ...s, shown: animation.to, animation: null } : s))
        return
      }
      const frameStats = lerpStats(animation.from, animation.to, easeOutCubic(progress))
      setState((s) => (s.animation === animation ? { ...s, shown: frameStats } : s))
      frame = requestAnimationFrame(step)
    }

    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [animation, durationMs])

  return state.shown
}
