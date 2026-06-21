import { Link, useNavigate } from "react-router-dom"
import type { TrendEntry } from "../types/trends"

type Props = {
  entries: TrendEntry[]
}

export function TrendRunEntries({ entries }: Props) {
  const navigate = useNavigate()

  if (entries.length === 0) {
    return <p className="text-gray-500 text-sm">No trends for this date.</p>
  }

  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="text-left bg-gray-100">
          <th className="p-2 border">Label</th>
          <th className="p-2 border">Name</th>
          <th className="p-2 border text-right">Count</th>
          <th className="p-2 border"></th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e, i) => (
          <tr
            key={i}
            className="hover:bg-gray-50 cursor-pointer"
            onClick={() => navigate(`/trends/history/${encodeURIComponent(e.label)}/${encodeURIComponent(e.name)}`)}
          >
            <td className="p-2 border text-gray-500">{e.label}</td>
            <td className="p-2 border font-medium">{e.name}</td>
            <td className="p-2 border text-right tabular-nums">{e.value}</td>
            <td className="p-2 border text-center">
              <Link
                to={`/recommendations?q=${encodeURIComponent(e.name)}`}
                className="text-blue-500 hover:underline text-xs"
                onClick={ev => ev.stopPropagation()}
              >
                Recs
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
