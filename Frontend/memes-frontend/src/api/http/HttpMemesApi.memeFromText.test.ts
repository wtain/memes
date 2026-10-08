import { HttpMemesApi } from "./HttpMemesApi"
import { MessageSearchFailedError, MessageSearchValidationError, isAbortError } from "../errors"
import { TEST_TAXONOMY, makeResponse } from "../../reply/fixtures"

const api = new HttpMemesApi("http://backend")

function stubFetch(result: { ok: boolean; status: number; body?: unknown } | Error) {
  const fetchMock = vi.fn().mockImplementation(async () => {
    if (result instanceof Error) throw result
    return { ok: result.ok, status: result.status, json: async () => result.body }
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe("searchMemeFromText", () => {
  it("posts the request as JSON with the abort signal and returns the parsed response", async () => {
    const body = makeResponse({ hasNext: true, nextCursor: "c1" })
    const fetchMock = stubFetch({ ok: true, status: 200, body })
    const controller = new AbortController()

    const result = await api.searchMemeFromText({ text: "hello", limit: 20 }, controller.signal)

    expect(result).toEqual(body)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("http://backend/api/meme-from-text/search")
    expect(init.method).toBe("POST")
    expect(init.headers).toMatchObject({ "Content-Type": "application/json", Accept: "application/json" })
    expect(JSON.parse(init.body)).toEqual({ text: "hello", limit: 20 })
    expect(init.signal).toBe(controller.signal)
  })

  it("maps a 422 to a validation error carrying only the messages, never the rejected input", async () => {
    stubFetch({
      ok: false, status: 422,
      body: { detail: [{ msg: "String should have at most 8000 characters", input: "SECRET-MESSAGE", loc: ["body", "text"] }, "emotion: unknown key 'x'"] },
    })

    const error = await api.searchMemeFromText({ text: "SECRET-MESSAGE" }).catch((e) => e)

    expect(error).toBeInstanceOf(MessageSearchValidationError)
    expect(error.details).toEqual(["String should have at most 8000 characters", "emotion: unknown key 'x'"])
    expect(JSON.stringify(error.details) + error.message).not.toContain("SECRET-MESSAGE")
  })

  it("maps other failures to a generic error with the status and no text", async () => {
    stubFetch({ ok: false, status: 500, body: { detail: "meme-from-text search failed" } })

    const error = await api.searchMemeFromText({ text: "SECRET-MESSAGE" }).catch((e) => e)

    expect(error).toBeInstanceOf(MessageSearchFailedError)
    expect(error.status).toBe(500)
    expect(error.message).not.toContain("SECRET-MESSAGE")
  })

  it("lets an abort through so the caller can recognise it", async () => {
    const abortError = new Error("aborted")
    Object.defineProperty(abortError, "name", { value: "AbortError" })
    stubFetch(abortError)

    const error = await api.searchMemeFromText({ text: "x" }).catch((e) => e)

    expect(isAbortError(error)).toBe(true)
    expect(isAbortError(new Error("other"))).toBe(false)
  })

  it("recognises a real DOMException abort as the browser raises it", () => {
    expect(isAbortError(new DOMException("aborted", "AbortError"))).toBe(true)
    expect(isAbortError(new DOMException("nope", "NetworkError"))).toBe(false)
  })

  it("survives a 422 whose body is not JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => { throw new Error("bad json") } })
    vi.stubGlobal("fetch", fetchMock)

    const error = await api.searchMemeFromText({ text: "x" }).catch((e) => e)

    expect(error).toBeInstanceOf(MessageSearchValidationError)
    expect(error.details).toEqual([])
  })
})

describe("getTaxonomy", () => {
  it("fetches the vocabulary", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: TEST_TAXONOMY })

    expect(await api.getTaxonomy()).toEqual(TEST_TAXONOMY)
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/meme-from-text/taxonomy")
  })

  it("throws on a failed response", async () => {
    stubFetch({ ok: false, status: 503 })
    await expect(api.getTaxonomy()).rejects.toThrow("Taxonomy failed: 503")
  })
})
