import { HttpMemesApi } from "./HttpMemesApi"

const api = new HttpMemesApi("http://backend")

afterEach(() => vi.unstubAllGlobals())

describe("getMemePopularity", () => {
  it("requests the popularity endpoint and returns the parsed body", async () => {
    const body = { popularity: 4, status: "normal", breakdown: [{ source: "own", count: 1 }] }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body })
    vi.stubGlobal("fetch", fetchMock)

    await expect(api.getMemePopularity("x")).resolves.toEqual(body)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/images/meme/x/popularity")
    expect(init.headers.Accept).toBe("application/json")
  })

  it("throws on a 404", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }))
    await expect(api.getMemePopularity("x")).rejects.toThrow("404")
  })
})
