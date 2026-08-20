import { useCallback, useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { DuplicateDecisionItem } from "../types/generated/all"

type Props = { memesApi: MemesApi }

const PAGE_SIZE = 20

export default function DuplicateDecisionsPanel({ memesApi }: Props) {
  const [items, setItems] = useState<DuplicateDecisionItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [undoing, setUndoing] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    return memesApi.listDuplicateDecisions(PAGE_SIZE, page * PAGE_SIZE)
      .then(res => { setItems(res.items); setTotal(res.total) })
      .finally(() => setLoading(false))
  }, [memesApi, page])

  useEffect(() => {
    void (async () => {
      await Promise.resolve()
      load()
    })()
  }, [load])

  function handleUndo(item: DuplicateDecisionItem) {
    const key = `${item.image_id1}:${item.image_id2}`
    setUndoing(key)
    memesApi.undoDismissDuplicates([{ image_id1: item.image_id1, image_id2: item.image_id2 }])
      .then(load)
      .finally(() => setUndoing(null))
  }

  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)

  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
      <h2 className="text-lg font-semibold mb-2">Not-duplicate decisions</h2>
      {loading ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="py-1">Image 1</th>
              <th>Image 2</th>
              <th>Decided</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => {
              const key = `${item.image_id1}:${item.image_id2}`
              return (
                <tr key={key} className="border-b last:border-0">
                  <td className="py-1">
                    <div className="flex items-center gap-2">
                      <img
                        src={memesApi.getImageUrlById(item.image_id1)}
                        alt={item.filename1}
                        className="w-10 h-10 object-cover rounded"
                        loading="lazy"
                      />
                      <span>{item.filename1}</span>
                    </div>
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      <img
                        src={memesApi.getImageUrlById(item.image_id2)}
                        alt={item.filename2}
                        className="w-10 h-10 object-cover rounded"
                        loading="lazy"
                      />
                      <span>{item.filename2}</span>
                    </div>
                  </td>
                  <td>{item.decided_at}</td>
                  <td>
                    <button
                      className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-50"
                      disabled={undoing === key}
                      onClick={() => handleUndo(item)}
                    >
                      Undo
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {!loading && items.length === 0 && <p className="text-sm text-gray-400 mt-2">No decisions yet.</p>}
      {total > 0 && (
        <div className="flex items-center gap-3 mt-3">
          <button
            className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-40"
            disabled={page === 0}
            onClick={() => setPage(p => Math.max(0, p - 1))}
          >
            Prev
          </button>
          <span className="text-xs text-gray-500">Page {page + 1} of {maxPage + 1}</span>
          <button
            className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-40"
            disabled={page >= maxPage}
            onClick={() => setPage(p => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
