import { HttpMemesApi } from "./HttpMemesApi"
import { ComicsRequestError } from "../errors"
import type { ComicsDetail } from "../../types/generated/all"

const api = new HttpMemesApi("http://backend")

function stubFetch(result: { ok: boolean; status: number; body?: unknown; jsonThrows?: boolean }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: result.ok,
    status: result.status,
    json: async () => {
      if (result.jsonThrows) throw new SyntaxError("not json")
      return result.body
    },
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

const detail = { id: "c1", name: "N", panels: [] } as unknown as ComicsDetail

afterEach(() => vi.unstubAllGlobals())

describe("comics API", () => {
  it("lists comics with q, offset and limit", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: { items: [], total: 0 } })
    await api.listComics("a b", 10, 5)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/comics?q=a+b&offset=10&limit=5")
  })

  it("lists comics with no query string by default", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: { items: [], total: 0 } })
    await api.listComics()
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/comics")
  })

  it("creates a comics", async () => {
    const fetchMock = stubFetch({ ok: true, status: 201, body: detail })
    expect(await api.createComics("N")).toEqual(detail)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/comics")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body)).toEqual({ name: "N" })
  })

  it("gets a comics", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: detail })
    await api.getComics("c1")
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/comics/c1")
  })

  it("renames a comics with PATCH", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: detail })
    await api.renameComics("c1", "New")
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/comics/c1")
    expect(init.method).toBe("PATCH")
    expect(JSON.parse(init.body)).toEqual({ name: "New" })
  })

  it("deletes a comics", async () => {
    const fetchMock = stubFetch({ ok: true, status: 204 })
    await expect(api.deleteComics("c1")).resolves.toBeUndefined()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/comics/c1")
    expect(init.method).toBe("DELETE")
  })

  it("adds a panel, with and without move", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: detail })
    await api.addComicsPanel("c1", "img")
    await api.addComicsPanel("c1", "img", true)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/comics/c1/panels")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body)).toEqual({ imageId: "img" })
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ imageId: "img", move: true })
  })

  it("removes a panel", async () => {
    const fetchMock = stubFetch({ ok: true, status: 204 })
    await expect(api.removeComicsPanel("c1", "p1")).resolves.toBeUndefined()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/comics/c1/panels/p1")
    expect(init.method).toBe("DELETE")
  })

  it("reorders panels with PUT", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: detail })
    await api.reorderComicsPanels("c1", ["b", "a"])
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/comics/c1/panels/order")
    expect(init.method).toBe("PUT")
    expect(JSON.parse(init.body)).toEqual({ panelIds: ["b", "a"] })
  })

  it("asks addability with comma-joined ids", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: { items: [] } })
    await api.comicsAddability(["a", "b"])
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/comics/addability?imageIds=a%2Cb")
  })

  it("gets the comics of an image", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: { comics: null } })
    await api.comicsByImage("img")
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/comics/by-image/img")
  })

  it("builds the preview url without fetching", () => {
    const fetchMock = stubFetch({ ok: true, status: 200 })
    expect(api.comicsPreviewUrl("c1", 400, "v7")).toBe("http://backend/api/comics/c1/preview?maxWidth=400&v=v7")
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("keeps code and detail of a 409 name_taken", async () => {
    stubFetch({ ok: false, status: 409, body: { detail: { code: "name_taken" } } })
    const error = await api.createComics("N").catch(e => e)
    expect(error).toBeInstanceOf(ComicsRequestError)
    expect(error.status).toBe(409)
    expect(error.code).toBe("name_taken")
  })

  it("keeps comicsId of a 409 image_in_comics", async () => {
    stubFetch({ ok: false, status: 409, body: { detail: { code: "image_in_comics", comicsId: "c9", comicsName: "X" } } })
    const error = await api.addComicsPanel("c1", "img").catch(e => e)
    expect(error.code).toBe("image_in_comics")
    expect(error.detail.comicsId).toBe("c9")
  })

  it("keeps reason of a 422 name_invalid", async () => {
    stubFetch({ ok: false, status: 422, body: { detail: { code: "name_invalid", reason: "empty" } } })
    const error = await api.renameComics("c1", "").catch(e => e)
    expect(error.status).toBe(422)
    expect(error.detail.reason).toBe("empty")
  })

  it("tolerates a non-JSON 500 and a string detail", async () => {
    stubFetch({ ok: false, status: 500, jsonThrows: true })
    const a = await api.getComics("c1").catch(e => e)
    expect(a).toBeInstanceOf(ComicsRequestError)
    expect(a.status).toBe(500)
    expect(a.code).toBeUndefined()
    expect(a.detail).toEqual({})

    stubFetch({ ok: false, status: 404, body: { detail: "Not Found" } })
    const b = await api.getComics("c1").catch(e => e)
    expect(b.code).toBeUndefined()
    expect(b.detail).toEqual({})
  })
})

describe("searchMemes record flag", () => {
  it("sends record=false when false", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: { items: [] } })
    await api.searchMemes({ query: "x", record: false })
    expect(fetchMock.mock.calls[0][0]).toContain("record=false")
  })

  it("sends nothing otherwise", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: { items: [] } })
    await api.searchMemes({ query: "x" })
    await api.searchMemes({ query: "x", record: true })
    expect(fetchMock.mock.calls[0][0]).not.toContain("record")
    expect(fetchMock.mock.calls[1][0]).not.toContain("record")
  })
})
