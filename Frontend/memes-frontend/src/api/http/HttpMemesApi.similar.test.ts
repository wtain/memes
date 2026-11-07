import { HttpMemesApi } from "./HttpMemesApi"

const api = new HttpMemesApi("http://backend")

afterEach(() => vi.unstubAllGlobals())

function stub() {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [] }) })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

describe("similarMemes", () => {
  it("keeps the old URLs when no options are given", async () => {
    const fetchMock = stub()
    await api.similarMemes("x")
    await api.similarMemes("x", "description")
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/images/x/similar")
    expect(fetchMock.mock.calls[1][0]).toBe("http://backend/api/images/x/similar?source=description")
  })

  it("sends limit and record=false when asked", async () => {
    const fetchMock = stub()
    await api.similarMemes("x", "image", { limit: 60, record: false })
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/images/x/similar?source=image&limit=60&record=false")
  })

  it("does not send record when it is true", async () => {
    const fetchMock = stub()
    await api.similarMemes("x", "image", { record: true })
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/images/x/similar?source=image")
  })
})
