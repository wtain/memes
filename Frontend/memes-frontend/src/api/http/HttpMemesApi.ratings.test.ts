import { HttpMemesApi } from "./HttpMemesApi"
import { RatingFailedError } from "../errors"

const api = new HttpMemesApi("http://backend")
const HASH = "a".repeat(64)

function stubFetch(result: { ok: boolean; status: number; body?: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: result.ok, status: result.status, json: async () => result.body,
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

const request = {
  text: "hello", imageId: "img-1", grade: 2, rank: 1, reading: "parsed" as const, parserVersion: "p3-x",
  taxonomyVersion: 2, degraded: false, fallback: "none", language: "en" as const,
}

describe("ratings API", () => {
  it("puts a rating as JSON and returns the stored rating", async () => {
    const body = { queryHash: HASH, imageId: "img-1", grade: 2 }
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    expect(await api.putRating(request)).toEqual(body)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/meme-from-text/ratings")
    expect(init.method).toBe("PUT")
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" })
    expect(JSON.parse(init.body)).toEqual(request)
  })

  it("deletes one rating by hash and image id", async () => {
    const fetchMock = stubFetch({ ok: true, status: 204 })
    await api.deleteRating(HASH, "img 1")
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(`http://backend/api/meme-from-text/ratings?queryHash=${HASH}&imageId=img+1`)
    expect(init.method).toBe("DELETE")
  })

  it("lists the grades of a message", async () => {
    const body = { queryHash: HASH, ratings: [{ imageId: "img-1", grade: 1 }] }
    const fetchMock = stubFetch({ ok: true, status: 200, body })
    expect(await api.getRatings(HASH)).toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toBe(`http://backend/api/meme-from-text/ratings?queryHash=${HASH}`)
  })

  it("forgets the stored text of a message", async () => {
    const fetchMock = stubFetch({ ok: true, status: 204 })
    await api.forgetRatingText(HASH)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(`http://backend/api/meme-from-text/ratings/text?queryHash=${HASH}`)
    expect(init.method).toBe("DELETE")
  })

  it.each([
    ["putRating", () => api.putRating(request)],
    ["deleteRating", () => api.deleteRating(HASH, "img-1")],
    ["getRatings", () => api.getRatings(HASH)],
    ["forgetRatingText", () => api.forgetRatingText(HASH)],
  ])("%s maps a failure to a fixed-message error with the status and no text", async (_name, call) => {
    stubFetch({ ok: false, status: 500, body: { detail: "rating request failed" } })
    const error = await call().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(RatingFailedError)
    expect((error as RatingFailedError).status).toBe(500)
    expect((error as Error).message).not.toContain("hello")
  })
})
