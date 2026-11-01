import { HttpMemesApi } from "./HttpMemesApi"

const api = new HttpMemesApi("http://backend")

afterEach(() => vi.unstubAllGlobals())

describe("reportNotFound", () => {
  it("posts to the not-found endpoint of the search", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 })
    vi.stubGlobal("fetch", fetchMock)

    await expect(api.reportNotFound("abc")).resolves.toBeUndefined()

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/history/abc/not-found")
    expect(init.method).toBe("POST")
  })

  it("throws on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }))
    await expect(api.reportNotFound("abc")).rejects.toThrow("500")
  })
})
