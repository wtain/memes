import { describeComicsError } from "./comicsErrors"
import { ComicsRequestError } from "../api/errors"

const err = (status: number, detail: Record<string, unknown> = {}) => new ComicsRequestError(status, detail)

describe("describeComicsError", () => {
  it.each([
    ["empty", "The name can't be empty"],
    ["too_long", "The name is too long"],
    ["forbidden_character", "The name contains a character that is not allowed"],
    ["trailing_dot_or_space", "The name can't end with a dot or a space"],
    ["reserved_name", "That name is reserved"],
  ])("explains name_invalid / %s", (reason, text) => {
    expect(describeComicsError(err(422, { code: "name_invalid", reason }))).toBe(text)
  })

  it("falls back for an unknown name_invalid reason", () => {
    expect(describeComicsError(err(422, { code: "name_invalid", reason: "weird" }))).toBe("That name is not allowed")
  })

  it("explains name_taken", () => {
    expect(describeComicsError(err(409, { code: "name_taken" }))).toBe("A comics with that name already exists")
  })

  it("explains image_in_comics, naming the comics when known", () => {
    expect(describeComicsError(err(409, { code: "image_in_comics", comicsName: "Cats" })))
      .toBe("That image is already a panel of the comics \"Cats\"")
    expect(describeComicsError(err(409, { code: "image_in_comics" }))).toBe("That image is already a panel of another comics")
  })

  it.each([
    ["not_active", "That image is not in the active library"],
    ["flagged", "That image is flagged"],
    ["live_cut", "That image has a recut cut, use the cut instead"],
    ["not_found", "That image no longer exists"],
    ["other", "That image can't be added"],
  ])("explains image_not_addable / %s", (reason, text) => {
    expect(describeComicsError(err(409, { code: "image_not_addable", reason }))).toBe(text)
  })

  it("explains built, not_found and reorder_mismatch", () => {
    expect(describeComicsError(err(409, { code: "built" }))).toBe("This comics is built and can't be changed")
    expect(describeComicsError(err(404, { code: "not_found" }))).toBe("That comics no longer exists")
    expect(describeComicsError(err(422, { code: "reorder_mismatch" })))
      .toBe("The panels changed in the meantime, reload and try again")
  })

  it.each([
    ["no_panels", null, "The comics has no panels yet"],
    ["unreadable", 2, "Panel 2 can't be read"],
    ["unreadable", null, "An image can't be read"],
    ["unsupported_format", 3, "Panel 3 has an unsupported image format"],
    ["animated", 1, "Panel 1 is animated"],
    ["too_large", null, "The comics would be too large"],
    ["mystery", 4, "The comics can't be composed (panel 4)"],
    ["mystery", null, "The comics can't be composed"],
  ])("explains compose_failed / %s at %s", (reason, position, text) => {
    expect(describeComicsError(err(422, { code: "compose_failed", reason, position }))).toBe(text)
  })

  it("has a generic fallback", () => {
    expect(describeComicsError(err(500))).toBe("Something went wrong, try again")
    expect(describeComicsError(new Error("x"))).toBe("Something went wrong, try again")
    expect(describeComicsError("nope")).toBe("Something went wrong, try again")
  })
})
