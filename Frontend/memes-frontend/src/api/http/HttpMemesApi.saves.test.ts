import { HttpMemesApi } from "./HttpMemesApi"

const api = new HttpMemesApi("http://backend")

afterEach(() => vi.unstubAllGlobals())

describe("getMemeSaves", () => {
  it("requests the saves endpoint and returns the parsed body", async () => {
    const body = { imageId: "x", count: 3, lastSavedAt: "2026-10-30T10:00:00Z" }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body })
    vi.stubGlobal("fetch", fetchMock)

    await expect(api.getMemeSaves("x")).resolves.toEqual(body)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/images/meme/x/saves")
    expect(init.headers.Accept).toBe("application/json")
  })

  it("throws on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 }))
    await expect(api.getMemeSaves("x")).rejects.toThrow("404")
  })
})
