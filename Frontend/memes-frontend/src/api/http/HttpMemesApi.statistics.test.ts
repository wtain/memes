import { afterEach, describe, expect, it, vi } from "vitest"
import { HttpMemesApi } from "./HttpMemesApi"

function stubFetch(ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({ ok, status, json: async () => ({}) })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

describe("HttpMemesApi.getStatistics", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("requests the plain URL by default", async () => {
    const fetchMock = stubFetch()
    await new HttpMemesApi("http://api").getStatistics()
    expect(fetchMock.mock.calls[0][0]).toBe("http://api/api/diagnostics/statistics")
  })

  it("appends ?live=true when live is requested", async () => {
    const fetchMock = stubFetch()
    await new HttpMemesApi("http://api").getStatistics({ live: true })
    expect(fetchMock.mock.calls[0][0]).toBe("http://api/api/diagnostics/statistics?live=true")
  })

  it("adds no query string when live is false", async () => {
    const fetchMock = stubFetch()
    await new HttpMemesApi("http://api").getStatistics({ live: false })
    expect(fetchMock.mock.calls[0][0]).toBe("http://api/api/diagnostics/statistics")
  })

  it("rejects with the status on a non-ok response", async () => {
    stubFetch(false, 500)
    await expect(new HttpMemesApi("http://api").getStatistics()).rejects.toThrow("Failed to fetch statistics: 500")
  })
})
