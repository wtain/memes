import { useSyncExternalStore } from "react"
import { Link } from "react-router-dom"
import { clearWorkingComics, getWorkingComics, subscribeWorkingComics } from "../../comics/workingComics"

/** Shows which comics the "Add to comics" clicks of a list go to; nothing while none is set. */
export function WorkingComicsBar() {
  const working = useSyncExternalStore(subscribeWorkingComics, getWorkingComics)
  if (working === null) return null
  return (
    <div className="mb-3 flex items-center gap-3 rounded border bg-gray-50 px-3 py-1 text-sm">
      <span>Adding to <strong>{working.name}</strong></span>
      <Link to={`/admin/comics/${working.id}`} className="text-blue-600 hover:underline">Open editor</Link>
      <button type="button" onClick={clearWorkingComics} className="px-2 py-0.5 border rounded cursor-pointer">Change</button>
      <button type="button" onClick={clearWorkingComics} className="px-2 py-0.5 border rounded cursor-pointer">Stop</button>
    </div>
  )
}
