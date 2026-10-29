import { HttpMemesApi } from "./HttpMemesApi"

const api = new HttpMemesApi("http://backend")

afterEach(() => vi.unstubAllGlobals())

describe("getTrendingMemes", () => {
  it("requests the trending endpoint with limit and cursor and returns the body", async () => {
    const body = { items: [], hasNext: false }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body })
    vi.stubGlobal("fetch", fetchMock)
    const result = await api.getTrendingMemes(24, "c1")
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/images/trending?limit=24&cursor=c1")
    expect(init.headers.Accept).toBe("application/json")
    expect(result).toBe(body)
  })

  it("omits absent parameters", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
    vi.stubGlobal("fetch", fetchMock)
    await api.getTrendingMemes()
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/images/trending")
  })

  it("throws on a non-2xx response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }))
    await expect(api.getTrendingMemes()).rejects.toThrow("500")
  })
})
