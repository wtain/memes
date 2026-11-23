import { HttpMemesApi } from "./HttpMemesApi"

describe("getShareUrlById", () => {
  it("builds the share url from an absolute base", () => {
    expect(new HttpMemesApi("http://localhost:8082").getShareUrlById("abc")).toBe("http://localhost:8082/api/share/abc")
  })

  it("does not double the slash for a base with a trailing slash", () => {
    expect(new HttpMemesApi("http://localhost:8082/").getShareUrlById("abc")).toBe("http://localhost:8082/api/share/abc")
  })

  it("resolves an empty base against the page origin", () => {
    expect(new HttpMemesApi("").getShareUrlById("abc")).toBe(`${window.location.origin}/api/share/abc`)
  })

  it("resolves a relative base against the page origin", () => {
    expect(new HttpMemesApi("/backend").getShareUrlById("abc")).toBe(`${window.location.origin}/backend/api/share/abc`)
  })
})
