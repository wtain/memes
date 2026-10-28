import { HttpMemesApi } from "./HttpMemesApi"

const api = new HttpMemesApi("http://backend")

function stubFetch() {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe("markImageIsFlagged", () => {
  it("sends reason and duplicateOf", async () => {
    const fetchMock = stubFetch()
    await api.markImageIsFlagged("x", "duplicate_review", "y")
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/images/meme/x/mark_flagged?reason=duplicate_review&duplicateOf=y")
    expect(init.method).toBe("PUT")
  })

  it("omits duplicateOf when not given", async () => {
    const fetchMock = stubFetch()
    await api.markImageIsFlagged("x", "duplicate_review")
    expect(fetchMock.mock.calls[0][0]).not.toContain("duplicateOf")
  })

  it("sends no query string without arguments", async () => {
    const fetchMock = stubFetch()
    await api.markImageIsFlagged("x")
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/images/meme/x/mark_flagged")
  })
})
