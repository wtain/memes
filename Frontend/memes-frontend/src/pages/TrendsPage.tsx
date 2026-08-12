import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import { TrendRunEntries } from "../components/TrendRunEntries"
import { TrendsCalendar } from "../components/TrendsCalendar"
import { useTrendsForDate } from "../utils/useTrendsForDate"

type Props = { memesApi: MemesApi }

export default function TrendsPage({ memesApi }: Props) {
  const [dates, setDates] = useState<string[]>([])
  const [latestDate, setLatestDate] = useState<string | null>(null)
  const datesLoadedRef = useRef(false)

  useEffect(() => {
    if (datesLoadedRef.current) return
    datesLoadedRef.current = true
    memesApi.getTrendsDates().then(d => {
      setDates(d)
      if (d.length > 0) setLatestDate(d[0])
    })
  }, [memesApi])

  const trendsState = useTrendsForDate(latestDate, memesApi)
  const dateSet = new Set(dates)

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-bold mb-1">Trends</h1>
        {latestDate && <p className="text-sm text-gray-500 mb-4">Latest successful run: {latestDate}</p>}

        {trendsState.status === "loading" && <p className="text-sm text-gray-400">Loading…</p>}
        {trendsState.status === "no-data" && <p className="text-sm text-gray-500">No trend data available.</p>}
        {trendsState.status === "error" && <p className="text-sm text-red-500">{trendsState.message}</p>}
        {trendsState.status === "done" && <TrendRunEntries entries={trendsState.entries} />}
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Calendar</h2>
        <TrendsCalendar availableDates={dateSet} selectedDate={latestDate ?? undefined} />
      </section>
    </div>
  )
}
