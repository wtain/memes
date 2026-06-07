import { useEffect, useRef, useState } from "react"
import { useParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import { TrendRunEntries } from "../components/TrendRunEntries"
import { TrendsCalendar } from "../components/TrendsCalendar"
import { useTrendsForDate } from "../utils/useTrendsForDate"

type Props = { memesApi: MemesApi }

export default function TrendsDatePage({ memesApi }: Props) {
  const { date } = useParams<{ date: string }>()
  const [dates, setDates] = useState<string[]>([])
  const datesLoadedRef = useRef(false)

  useEffect(() => {
    if (datesLoadedRef.current) return
    datesLoadedRef.current = true
    memesApi.getTrendsDates().then(setDates)
  }, [memesApi])

  const trendsState = useTrendsForDate(date ?? null, memesApi)
  const dateSet = new Set(dates)

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-bold mb-1">Trends</h1>
        {date && <p className="text-sm text-gray-500 mb-4">{date}</p>}

        {trendsState.status === "loading" && <p className="text-sm text-gray-400">Loading…</p>}
        {trendsState.status === "no-data" && <p className="text-sm text-gray-500">No trend data for this date.</p>}
        {trendsState.status === "error" && <p className="text-sm text-red-500">{trendsState.message}</p>}
        {trendsState.status === "done" && <TrendRunEntries entries={trendsState.entries} />}
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Calendar</h2>
        <TrendsCalendar availableDates={dateSet} selectedDate={date} />
      </section>
    </div>
  )
}
