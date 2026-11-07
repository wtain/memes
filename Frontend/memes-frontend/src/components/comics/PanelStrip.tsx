import type { ComicsPanel } from "../../types/generated/all"

type Props = {
  panels: ComicsPanel[]
  imageUrl: (imageId: string) => string
  busy: boolean
  onMove: (index: number, delta: -1 | 1) => void
  onRemove: (panelId: string) => void
  onNeighbours: (panelId: string, imageId: string) => void
}

const BUTTON = "min-h-11 min-w-11 rounded border px-2 disabled:opacity-50"

export default function PanelStrip({ panels, imageUrl, busy, onMove, onRemove, onNeighbours }: Props) {
  return (
    <ol aria-label="Panels" className="flex flex-col gap-2">
      {panels.map((panel, index) => {
        const n = index + 1
        return (
          <li key={panel.id} className="flex items-center gap-2 rounded border p-2">
            <span className="w-6 text-center font-medium">{n}</span>
            {panel.imageId !== null ? (
              <img src={imageUrl(panel.imageId)} alt={`Panel ${n}`} className="h-16 w-16 shrink-0 rounded object-cover" />
            ) : (
              <div className="h-16 w-16 shrink-0 rounded bg-gray-200" aria-hidden="true" />
            )}
            <span className="min-w-0 flex-1 truncate text-sm">{panel.sourceFilename}</span>
            <button type="button" aria-label={`Move panel ${n} up`} disabled={busy || index === 0}
                    onClick={() => onMove(index, -1)} className={BUTTON}>Up</button>
            <button type="button" aria-label={`Move panel ${n} down`} disabled={busy || index === panels.length - 1}
                    onClick={() => onMove(index, 1)} className={BUTTON}>Down</button>
            {panel.imageId !== null && (
              <button type="button" aria-label={`Find neighbours of panel ${n}`} disabled={busy}
                      onClick={() => onNeighbours(panel.id, panel.imageId as string)} className={BUTTON}>Neighbours</button>
            )}
            <button type="button" aria-label={`Remove panel ${n}`} disabled={busy}
                    onClick={() => onRemove(panel.id)} className={BUTTON}>Remove</button>
          </li>
        )
      })}
    </ol>
  )
}
