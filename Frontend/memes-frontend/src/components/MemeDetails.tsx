import { useId, useState, type ReactNode } from "react"
import { TagList } from "./TagList"
import { PopularityBlock } from "./PopularityBlock"
import { SaveCountBlock } from "./SaveCountBlock"
import { ImpressionBlock } from "./ImpressionBlock"
import { MemeComicsBlock } from "./comics/MemeComicsBlock"
import type { MemesApi } from "../api/MemesApi"
import MemeCard from "./MemeCard"
import { MemeActions } from "./MemeActions"
import { MemeImageViewer } from "./MemeImageViewer"
import type { Concept, ImageDescription, Meme } from "../types/generated/all"
import { ConceptRow } from "./ConceptRow"
import { useNavigate } from "react-router-dom"
import { useFetchById } from "../utils/useFetchById"
import { useFeature } from "../features/useFeature"
import { Tabs, panelId, tabId } from "../ui"

type Props = {
  meme: Meme
  memesApi: MemesApi
  /** "modal": the two columns scroll independently from `lg` up; "page": the page scrolls. */
  layout?: "modal" | "page"
  /** Rendered inside the image box (the quick-look arrows). */
  imageOverlay?: ReactNode
}

function humanizePromptKey(promptKey: string): string {
  const spaced = promptKey.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

type TabPanels = { info: ReactNode; similar: ReactNode; stats: ReactNode; curate: ReactNode | null }

// Keyed by meme id in MemeDetails, so the selected tab resets to Info when the meme changes.
// The panels are rendered by the caller (their data hooks stay there) and are always mounted:
// an inactive one is only hidden.
function PanelTabs({ panels }: { panels: TabPanels }) {
  const [active, setActive] = useState("info")
  const idPrefix = useId()

  const tabs = [
    { id: "info", label: "Info", panel: panels.info },
    { id: "similar", label: "Similar", panel: panels.similar },
    { id: "stats", label: "Stats", panel: panels.stats },
    ...(panels.curate !== null ? [{ id: "curate", label: "Curate", panel: panels.curate }] : []),
  ]

  return (
    <div>
      <Tabs
        tabs={tabs.map(({ id, label }) => ({ id, label }))}
        value={active}
        onChange={setActive}
        idPrefix={idPrefix}
        aria-label="Meme information"
      />
      {tabs.map(t => (
        <div
          key={t.id}
          role="tabpanel"
          id={panelId(idPrefix, t.id)}
          aria-labelledby={tabId(idPrefix, t.id)}
          hidden={t.id !== active}
          className="pt-4 text-sm space-y-4"
        >
          {t.panel}
        </div>
      ))}
    </div>
  )
}

export function MemeDetails({ meme, memesApi, layout = "page", imageOverlay }: Props) {
  const [similarMemes, setSimilarMemes] = useState<Meme[]>([])
  const [similarSource, setSimilarSource] = useState<"image" | "description">("image")
  const [concepts, setConcepts] = useState<Concept[]>([])
  const [descriptions, setDescriptions] = useState<ImageDescription[]>([])
  const [isFlagged, setIsFlagged] = useState<boolean | null>(null)
  const [popularityVersion, setPopularityVersion] = useState(0)
  const [noteText, setNoteText] = useState<string | null>(null)

  // The per-meme state belongs to one meme: reset it during render when the meme changes (no flash of
  // the previous meme's data, and a failed fetch cannot leave it behind). similarSource carries over.
  const [shownId, setShownId] = useState(meme.id)
  if (shownId !== meme.id) {
    setShownId(meme.id)
    setSimilarMemes([])
    setConcepts([])
    setDescriptions([])
    setIsFlagged(null)
    setNoteText(null)
  }

  const navigate = useNavigate()
  const curator = useFeature("curator")
  const comicsOn = useFeature("comics")

  useFetchById(
    `${meme.id}:${similarSource}`,
    () => memesApi.similarMemes(meme.id, similarSource),
    resp => setSimilarMemes(resp.items ?? []),
    () => setSimilarMemes([]),
  )
  useFetchById(meme.id, id => memesApi.getTopConceptsForImage(id), resp => setConcepts(resp ?? []))
  useFetchById(meme.id, id => memesApi.getDescriptions(id), setDescriptions)
  // The flag state and the note belong to the curator controls; a prod build neither shows nor reads them.
  useFetchById(curator ? meme.id : undefined, id => memesApi.getImageIsFlagged(id), setIsFlagged)
  useFetchById(curator ? meme.id : undefined, id => memesApi.getMeme(id), m => setNoteText(m.descriptionNote ?? ""))

  function toggleFlagged() {
    const next = !isFlagged
    const call = next ? memesApi.markImageIsFlagged(meme.id) : memesApi.unmarkImageIsFlagged(meme.id)
    call.then(() => {
      setIsFlagged(next)
      // Flagging changes the status and unflagging can restore popularity; refetch the block.
      setPopularityVersion(v => v + 1)
    })
  }

  function setDescriptionFeedback(promptKey: string, action: "approve" | "reject") {
    memesApi.setDescriptionFeedback(meme.id, promptKey, action).then(resp => {
      setDescriptions(prev => prev.map(d =>
        d.promptKey === promptKey ? { ...d, feedback: resp.feedback } : d
      ))
    })
  }

  function saveNote() {
    if (noteText === null) return  // guards the disabled-while-loading window
    memesApi.setDescriptionNote(meme.id, noteText)
  }

  function clearNote() {
    memesApi.deleteDescriptionNote(meme.id).then(() => setNoteText(""))
  }

  const infoPanel = (
    <>
      <div>
        <strong>ID:</strong>
          <a className="hover:underline" href={`/memes/${meme.id}`}>{meme.id}</a>
          <span
              onClick={() => {
              if ("clipboard" in navigator) navigator.clipboard.writeText(meme.id!)
              }}
              className="cursor-pointer hover:bg-gray-100 transition ml-2"
          >🗐</span>
          <br />
        <div
          onClick={() => {
            if ("clipboard" in navigator) navigator.clipboard.writeText(meme.originalFileName!)
          }}
          className="cursor-pointer hover:bg-gray-100 transition"
        >
          <strong>File name: </strong>{meme.originalFileName} <br />
        </div>
      </div>

      <div>
        <strong>Text Lines:</strong>
        <ul className="list-disc ml-6">
          {meme.text!.map((line, i) => <li key={i}>{line}</li>)}
        </ul>
      </div>

      <div>
        <strong>Descriptions:</strong>
        {descriptions.length === 0 ? (
          <p className="text-gray-400">No description available</p>
        ) : (
          <ul className="ml-2 space-y-2">
            {descriptions.map(d => (
              <li key={d.promptKey}>
                <span className="font-medium">{humanizePromptKey(d.promptKey)}:</span> {d.text}
                {curator && <button
                  onClick={() => setDescriptionFeedback(d.promptKey, "approve")}
                  aria-label={`Approve ${humanizePromptKey(d.promptKey)}`}
                  className={`ml-2 px-1.5 py-0.5 text-xs rounded border ${d.feedback === "approved" ? "bg-green-600 text-white border-green-600" : "border-gray-300 text-gray-500 hover:bg-gray-100"}`}
                >
                  👍
                </button>}
                {curator && <button
                  onClick={() => setDescriptionFeedback(d.promptKey, "reject")}
                  aria-label={`Reject ${humanizePromptKey(d.promptKey)}`}
                  className={`ml-1 px-1.5 py-0.5 text-xs rounded border ${d.feedback === "rejected" ? "bg-red-600 text-white border-red-600" : "border-gray-300 text-gray-500 hover:bg-gray-100"}`}
                >
                  👎
                </button>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <strong>Tags:</strong>
        <TagList tags={meme.tags!} />
      </div>
    </>
  )

  const similarPanel = (
    <div>
      <div className="flex gap-2 mb-2">
        <button
          onClick={() => setSimilarSource("image")}
          className={`px-3 py-1 text-xs rounded border ${similarSource === "image" ? "bg-gray-800 text-white border-gray-800" : "border-gray-300 text-gray-600 hover:bg-gray-100"}`}
        >
          Visual
        </button>
        <button
          onClick={() => setSimilarSource("description")}
          className={`px-3 py-1 text-xs rounded border ${similarSource === "description" ? "bg-gray-800 text-white border-gray-800" : "border-gray-300 text-gray-600 hover:bg-gray-100"}`}
        >
          Semantic
        </button>
      </div>
      {similarMemes.length === 0 ? (
        <p className="text-gray-400 text-sm">
          {similarSource === "description"
            ? "No semantic similarity available for this image yet"
            : "No similar images found"}
        </p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {similarMemes.map(m => (
            <div key={m.id}>
              <MemeCard meme={m} memesApi={memesApi} onClick={() => navigate(`/memes/${m.id}`)} comicsAction />
              {typeof m.cosineDistance === "number" && (
                <p className="text-center text-xs text-gray-400 mt-1">{m.cosineDistance.toFixed(2)}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )

  const statsPanel = (
    <>
      <PopularityBlock key={`${meme.id}:${popularityVersion}`} imageId={meme.id} memesApi={memesApi} />
      <SaveCountBlock key={meme.id} imageId={meme.id} memesApi={memesApi} />
      <ImpressionBlock key={`impressions:${meme.id}`} imageId={meme.id} memesApi={memesApi} />
      {comicsOn && <MemeComicsBlock key={`comics:${meme.id}`} imageId={meme.id} memesApi={memesApi} />}

      {concepts.length > 0 && (
        <div>
          <table className="w-full border">
            <thead>
              <tr className="text-left bg-gray-100">
                <th className="p-3">ID</th>
                <th className="p-3">Name</th>
              </tr>
            </thead>
            <tbody>
              {concepts.map(concept => (
                <ConceptRow key={concept.id} concept={concept} onClick={() => navigate(`/concepts/${concept.id}`)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )

  // No permission gating yet -- see docs/security/admin-permissions-todo.md
  const curatePanel = curator ? (
    <>
      <div>
        <label htmlFor="description-note" className="block mb-1">
          <strong>Description note:</strong>
        </label>
        <textarea
          id="description-note"
          aria-label="Description note"
          value={noteText ?? ""}
          disabled={noteText === null}
          onChange={e => setNoteText(e.target.value)}
          className="w-full border rounded p-2 text-sm disabled:opacity-50"
          rows={3}
        />
        <div className="flex gap-2 mt-1">
          <button
            onClick={saveNote}
            aria-label="Save note"
            disabled={noteText === null}
            className="px-3 py-1 text-xs rounded border border-gray-300 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Save note
          </button>
          <button
            onClick={clearNote}
            aria-label="Clear note"
            disabled={noteText === null}
            className="px-3 py-1 text-xs rounded border border-gray-300 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Clear note
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input
          id="flagged-toggle"
          type="checkbox"
          checked={isFlagged ?? false}
          disabled={isFlagged === null}
          onChange={toggleFlagged}
          className="w-4 h-4 cursor-pointer"
        />
        <label htmlFor="flagged-toggle" className="cursor-pointer select-none">
          Flagged
        </label>
      </div>
    </>
  ) : null

  const modal = layout === "modal"

  return (
    <div className={`grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] ${modal ? "lg:max-h-[calc(90vh-6rem)]" : ""}`}>
      <div className={modal ? "min-w-0 lg:overflow-y-auto" : "min-w-0 lg:sticky lg:top-16 lg:self-start"}>
        <MemeImageViewer
          src={memesApi.getImageUrl(meme)}
          alt={meme.id}
          resetKey={meme.id}
          overlay={imageOverlay}
          imageMaxHeight={modal
            ? "max-h-[min(75vh,calc(90vh-6rem))]"
            : "max-h-[75vh] lg:max-h-[calc(100vh-6rem)]"}
        />
      </div>

      <div className={`min-w-0 text-sm ${modal ? "lg:overflow-y-auto lg:max-h-[calc(90vh-6rem)]" : ""}`}>
        <div className="mb-4">
          <MemeActions meme={meme} memesApi={memesApi} layout="row" />
        </div>
        <PanelTabs
          key={meme.id}
          panels={{ info: infoPanel, similar: similarPanel, stats: statsPanel, curate: curatePanel }}
        />
      </div>
    </div>
  )
}
