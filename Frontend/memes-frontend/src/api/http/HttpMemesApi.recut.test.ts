import { HttpMemesApi } from "./HttpMemesApi"
import { CuratorRequestError } from "../errors"
import { makeProposalDetail, makeProposalPage } from "../../recut/fixtures"

const api = new HttpMemesApi("http://backend")

function stubFetch(result: { ok: boolean; status: number; body?: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: result.ok, status: result.status, json: async () => result.body })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe("recut API", () => {
  it("lists proposals with offset and limit", async () => {
    const body = makeProposalPage(["a", "b"], 9)
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    const result = await api.listRecutProposals(50, 25)

    expect(result).toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/recut/proposals?offset=50&limit=25")
  })

  it("lists proposals without a query string by default", async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: makeProposalPage([]) })

    await api.listRecutProposals()

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/recut/proposals")
  })

  it("gets one proposal by id", async () => {
    const body = makeProposalDetail("a")
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    expect(await api.getRecutProposal("a")).toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/recut/proposals/a")
  })

  it("rejects with the HTTP status on failure", async () => {
    stubFetch({ ok: false, status: 404 })

    await expect(api.getRecutProposal("zzz")).rejects.toMatchObject({ name: "CuratorRequestError", status: 404 })
    await expect(api.getRecutProposal("zzz")).rejects.toBeInstanceOf(CuratorRequestError)
  })
})
