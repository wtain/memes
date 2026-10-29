import { HttpMemesApi } from "./HttpMemesApi"

const api = new HttpMemesApi("http://backend")

afterEach(() => vi.unstubAllGlobals())

describe("setMemePopularity", () => {
  it("PUTs the target as JSON and returns the parsed body", async () => {
    const body = { popularity: 30, status: "normal", breakdown: [{ source: "own", count: 1 }, { source: "adjustment", count: 28 }] }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body })
    vi.stubGlobal("fetch", fetchMock)

    await expect(api.setMemePopularity("x", 30)).resolves.toEqual(body)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/images/meme/x/popularity")
    expect(init.method).toBe("PUT")
    expect(init.headers["Content-Type"]).toBe("application/json")
    expect(JSON.parse(init.body)).toEqual({ popularity: 30 })
  })

  it("throws on a 409", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409 }))
    await expect(api.setMemePopularity("x", 3)).rejects.toThrow("409")
  })
})
