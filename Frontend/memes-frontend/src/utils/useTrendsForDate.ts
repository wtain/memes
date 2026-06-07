import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { TrendEntry } from "../types/trends"

type State =
  | { status: "loading" }
  | { status: "no-data" }
  | { status: "done"; entries: TrendEntry[]; runDate: string }
  | { status: "error"; message: string }

export function useTrendsForDate(date: string | null, memesApi: MemesApi): State {
  const [state, setState] = useState<State>({ status: "loading" })
  const lastDateRef = useRef<string | null>(null)

  useEffect(() => {
    if (date === null) return
    if (lastDateRef.current === date) return
    lastDateRef.current = date
    const currentDate = date

    void (async () => {
      await Promise.resolve()
      setState({ status: "loading" })
      try {
        const run = await memesApi.getLatestTrendsRun(currentDate)
        const entries = await memesApi.getTrendsRun(run.runId)
        if (lastDateRef.current === currentDate) setState({ status: "done", entries, runDate: currentDate })
      } catch (err) {
        if (lastDateRef.current !== currentDate) return
        const msg: string = err instanceof Error ? err.message : String(err)
        setState(msg.includes("404") ? { status: "no-data" } : { status: "error", message: msg })
      }
    })()
  }, [date, memesApi])

  return state
}
