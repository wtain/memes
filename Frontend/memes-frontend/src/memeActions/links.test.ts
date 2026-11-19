import { expect, it } from "vitest"
import { memePageUrl } from "./links"

it("builds an absolute page url on the page origin", () => {
  expect(memePageUrl("abc")).toBe(new URL("/memes/abc", window.location.origin).href)
})
