import { render, screen, within, fireEvent, waitFor } from "@testing-library/react"
import { StrictMode } from "react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import AppLayout from "../app/AppLayout"
import type { CapabilitiesFeatures } from "../types/generated/all"
import { fetchCapabilities } from "./capabilities"
import { FeaturesProvider } from "./FeaturesProvider"
import type { Stage } from "./visibility"

const ALL_ON: CapabilitiesFeatures = {
  memeFromText: true, concepts: true, trends: true, comics: true, ratings: true,
  statistics: true, uploads: true, history: true, admin: true,
}

const PATHS = [
  "/untagged", "/no-ocr", "/flagged", "/duplicates", "/ingestion", "/statistics", "/trends",
  "/trends/date/2026-01-01", "/trends/history/PERSON/x", "/reply", "/upload", "/admin", "/admin/profiles",
  "/admin/recut", "/admin/discovery", "/admin/comics", "/admin/comics/candidates", "/admin/comics/c1",
]

function renderApp(path: string, stage: Stage, load?: () => Promise<CapabilitiesFeatures | null>) {
  return render(
    <StrictMode>
      <FeaturesProvider stage={stage} loadCapabilities={load}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/explore" element={<div>explore-content</div>} />
              <Route path="/concepts" element={<div>concepts-content</div>} />
              <Route path="/search" element={<div>search-content</div>} />
              {[...PATHS, "/trends/date/:date", "/trends/history/:label/:name", "/admin/comics/:id"].map(p => (
                <Route key={p} path={p} element={<div>page-{p}</div>} />
              ))}
            </Route>
          </Routes>
        </MemoryRouter>
      </FeaturesProvider>
    </StrictMode>,
  )
}

function groupButtons() {
  return within(screen.getByRole("navigation", { name: "Main" })).getAllByRole("button").map(b => b.textContent?.replace("▾", "").trim())
}

function open(group: string) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${group}`) }))
}

describe("menu on a prod build", () => {
  it("drops the Cleanup and Admin groups and the hidden entries of Find and Library", async () => {
    renderApp("/explore", "prod", () => Promise.resolve(null))
    expect(groupButtons()).toEqual(["Find", "Library"])

    open("Find")
    const find = within(screen.getByRole("menu", { name: "Find" })).getAllByRole("menuitem").map(i => i.textContent)
    expect(find).toEqual(["Search", "Recommendations", "Explore", "Trending memes"])
    // Trends is gone; the separator still divides Explore from Trending memes
    expect(within(screen.getByRole("menu", { name: "Find" })).queryAllByRole("separator")).toHaveLength(1)

    open("Library")
    const library = within(screen.getByRole("menu", { name: "Library" })).getAllByRole("menuitem").map(i => i.textContent)
    expect(library).toEqual(["Concepts"])
  })

  it("lets a capability take Concepts away, and the Library group with it", async () => {
    renderApp("/explore", "prod", () => Promise.resolve({ ...ALL_ON, concepts: false }))
    await waitFor(() => expect(groupButtons()).toEqual(["Find"]))
  })

  it("keeps the build menu when the capabilities call fails", async () => {
    const load = vi.fn().mockRejectedValue(new Error("down"))
    renderApp("/explore", "prod", load)
    await waitFor(() => expect(load).toHaveBeenCalled())
    expect(groupButtons()).toEqual(["Find", "Library"])
  })

  it("reads the capabilities once per mount of the provider's lifetime (StrictMode double effect is aborted, not stacked)", async () => {
    const load = vi.fn().mockResolvedValue(null)
    renderApp("/explore", "prod", load)
    await waitFor(() => expect(load).toHaveBeenCalled())
    // StrictMode runs the effect twice on mount; the first run is aborted by its cleanup.
    expect(load.mock.calls.length).toBeLessThanOrEqual(2)
  })
})

describe("menu on a dev build", () => {
  it("shows every group and never asks the backend for capabilities", () => {
    const load = vi.fn().mockResolvedValue({ ...ALL_ON, admin: false, concepts: false })
    renderApp("/explore", "dev", load)
    expect(groupButtons()).toEqual(["Find", "Library", "Cleanup", "Admin"])
    expect(load).not.toHaveBeenCalled()
  })

  it("keeps hidden-on-prod routes reachable", () => {
    renderApp("/admin/profiles", "dev")
    expect(screen.getByText("page-/admin/profiles")).toBeInTheDocument()
  })
})

describe("route redirects", () => {
  it.each(PATHS)("%s redirects to /explore on a prod build", path => {
    renderApp(path, "prod", () => Promise.resolve(ALL_ON))
    expect(screen.getByText("explore-content")).toBeInTheDocument()
    expect(screen.queryByText(`page-${path}`)).not.toBeInTheDocument()
  })

  it("leaves the browse routes alone on a prod build", () => {
    renderApp("/search", "prod", () => Promise.resolve(null))
    expect(screen.getByText("search-content")).toBeInTheDocument()
  })

  it("redirects /concepts once a capability turns it off", async () => {
    renderApp("/concepts", "prod", () => Promise.resolve({ ...ALL_ON, concepts: false }))
    await waitFor(() => expect(screen.getByText("explore-content")).toBeInTheDocument())
  })
})

describe("fetchCapabilities", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("returns the features of a good answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true, json: () => Promise.resolve({ stage: "prod", environment: "it", features: ALL_ON }),
    }))
    await expect(fetchCapabilities("http://x")).resolves.toEqual(ALL_ON)
    expect(fetch).toHaveBeenCalledWith("http://x/api/diagnostics/capabilities", expect.anything())
  })

  it("returns null on 404, a network error and a malformed body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }))
    await expect(fetchCapabilities("http://x")).resolves.toBeNull()
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")))
    await expect(fetchCapabilities("http://x")).resolves.toBeNull()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ nope: 1 }) }))
    await expect(fetchCapabilities("http://x")).resolves.toBeNull()
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.reject(new Error("bad json")) }))
    await expect(fetchCapabilities("http://x")).resolves.toBeNull()
  })
})
