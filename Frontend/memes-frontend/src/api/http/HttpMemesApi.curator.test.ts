import { HttpMemesApi } from "./HttpMemesApi"
import { CuratorRequestError } from "../errors"
import { makeCuratedProfile, makeCoverage, makeCoverageImages } from "../../curator/fixtures"

const api = new HttpMemesApi("http://backend")

function stubFetch(result: { ok: boolean; status: number; body?: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: result.ok, status: result.status, json: async () => result.body,
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe("curator API", () => {
  it("gets a curated profile by image id", async () => {
    const body = makeCuratedProfile()
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    const result = await api.getCuratedProfile("img-1")

    expect(result).toEqual(body)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/meme-from-text/profiles/img-1")
  })

  it("puts a profile as JSON and returns the body", async () => {
    const body = makeCuratedProfile({ situation: "new situation" })
    const fetchMock = stubFetch({ ok: true, status: 200, body })
    const request = { situation: "new situation", useWhen: "", values: { emotion: [], stance: [], topic: [], tone: [] } }

    const result = await api.saveCuratedProfile("img-1", request)

    expect(result).toEqual(body)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/meme-from-text/profiles/img-1")
    expect(init?.method).toBe("PUT")
    expect(init?.headers).toMatchObject({ "Content-Type": "application/json" })
    expect(JSON.parse(init?.body as string)).toEqual(request)
  })

  it("confirms a profile", async () => {
    const body = makeCuratedProfile({ humanVerified: true })
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    const result = await api.confirmCuratedProfile("img-1")

    expect(result).toEqual(body)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/meme-from-text/profiles/img-1/confirm")
    expect(init?.method).toBe("POST")
  })

  it("resets a profile and returns undefined on 204", async () => {
    const fetchMock = stubFetch({ ok: true, status: 204 })

    const result = await api.resetCuratedProfile("img-1")

    expect(result).toBeUndefined()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/meme-from-text/profiles/img-1")
    expect(init?.method).toBe("DELETE")
  })

  it("gets profile coverage", async () => {
    const body = makeCoverage()
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    const result = await api.getProfileCoverage()

    expect(result).toEqual(body)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/admin/profile-coverage")
  })

  it("gets profile coverage images with category", async () => {
    const body = makeCoverageImages(["img-1", "img-2"])
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    const result = await api.getProfileCoverageImages("human_verified")

    expect(result).toEqual(body)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/admin/profile-coverage/images?category=human_verified")
  })

  it("gets profile coverage images with category and cursor", async () => {
    const body = makeCoverageImages(["img-3"], "next-cursor")
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    const result = await api.getProfileCoverageImages("human_verified", "prev-cursor")

    expect(result).toEqual(body)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/admin/profile-coverage/images?category=human_verified&cursor=prev-cursor")
  })

  it("gets profile coverage images with category, cursor and limit", async () => {
    const body = makeCoverageImages(["img-4"])
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    const result = await api.getProfileCoverageImages("human_verified", "cursor", 50)

    expect(result).toEqual(body)
    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/admin/profile-coverage/images?category=human_verified&cursor=cursor&limit=50")
  })

  it("encodes image ids in URLs", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: makeCuratedProfile() })

    await api.getCuratedProfile("img with spaces")

    const [url] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/meme-from-text/profiles/img%20with%20spaces")
  })

  it.each([
    ["getCuratedProfile", () => api.getCuratedProfile("img-1")],
    ["saveCuratedProfile", () => api.saveCuratedProfile("img-1", { situation: "", useWhen: "", values: { emotion: [], stance: [], topic: [], tone: [] } })],
    ["confirmCuratedProfile", () => api.confirmCuratedProfile("img-1")],
    ["resetCuratedProfile", () => api.resetCuratedProfile("img-1")],
    ["getProfileCoverage", () => api.getProfileCoverage()],
    ["getProfileCoverageImages", () => api.getProfileCoverageImages("human_verified")],
  ])("%s maps a failure to a fixed-message error with the status and no text", async (_name, call) => {
    stubFetch({ ok: false, status: 500, body: { detail: "something went wrong" } })
    const error = await call().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(CuratorRequestError)
    expect((error as CuratorRequestError).status).toBe(500)
    expect((error as Error).message).not.toContain("something")
  })
})
