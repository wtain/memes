import { useState } from "react"
import { useNavigate } from "react-router-dom"

type Props = {
  availableDates: Set<string>  // YYYY-MM-DD strings
  selectedDate?: string
}

function daysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate()
}

function firstDayOfWeek(year: number, month: number) {
  return new Date(year, month, 1).getDay() // 0=Sun
}

function toYMD(year: number, month: number, day: number) {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`
}

const MONTH_NAMES = ["January","February","March","April","May","June","July","August","September","October","November","December"]
const DAY_NAMES = ["Su","Mo","Tu","We","Th","Fr","Sa"]

export function TrendsCalendar({ availableDates, selectedDate }: Props) {
  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [month, setMonth] = useState(today.getMonth())
  const navigate = useNavigate()

  const days = daysInMonth(year, month)
  const startDow = firstDayOfWeek(year, month)
  const cells: (number | null)[] = [...Array(startDow).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)]
  // pad to full weeks
  while (cells.length % 7 !== 0) cells.push(null)

  function prevMonth() {
    if (month === 0) { setYear(y => y - 1); setMonth(11) }
    else setMonth(m => m - 1)
  }

  function nextMonth() {
    if (month === 11) { setYear(y => y + 1); setMonth(0) }
    else setMonth(m => m + 1)
  }

  return (
    <div className="inline-block select-none">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <button onClick={prevMonth} className="px-2 py-0.5 rounded hover:bg-gray-100 text-gray-600">‹</button>
        <span className="font-medium text-sm">{MONTH_NAMES[month]} {year}</span>
        <button onClick={nextMonth} className="px-2 py-0.5 rounded hover:bg-gray-100 text-gray-600">›</button>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-7 gap-0.5 text-xs">
        {DAY_NAMES.map(d => (
          <div key={d} className="text-center text-gray-400 py-1 font-medium">{d}</div>
        ))}
        {cells.map((day, i) => {
          if (day === null) return <div key={i} />
          const ymd = toYMD(year, month, day)
          const hasData = availableDates.has(ymd)
          const isSelected = ymd === selectedDate
          return (
            <button
              key={i}
              disabled={!hasData}
              onClick={() => navigate(`/trends/date/${ymd}`)}
              className={[
                "w-8 h-8 rounded text-center leading-8",
                isSelected ? "bg-blue-600 text-white font-semibold" : "",
                hasData && !isSelected ? "bg-blue-100 text-blue-800 hover:bg-blue-200 font-medium" : "",
                !hasData ? "text-gray-300 cursor-default" : "cursor-pointer",
              ].join(" ")}
            >
              {day}
            </button>
          )
        })}
      </div>
    </div>
  )
}
