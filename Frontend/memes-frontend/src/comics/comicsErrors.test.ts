import { describeCandidateError, describeComicsError, isCandidateGone } from "./comicsErrors"
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

  it("explains candidate_decided and subset_invalid", () => {
    expect(describeComicsError(err(409, { code: "candidate_decided", state: "accepted" })))
      .toBe("This suggestion was already decided, reload the page")
    expect(describeComicsError(err(422, { code: "subset_invalid", reason: "too_few_images" })))
      .toBe("Pick at least two images")
    expect(describeComicsError(err(422, { code: "subset_invalid", reason: "unknown_image" })))
      .toBe("The images changed in the meantime, reload the page")
  })

  it("explains candidate_incomplete", () => {
    expect(describeComicsError(err(409, { code: "candidate_incomplete", size: 3, remaining: 2 })))
      .toBe("Some images of this suggestion no longer exist")
  })

  it("describeCandidateError scopes not_found to the suggestion and names the image", () => {
    expect(describeCandidateError(err(404, { code: "not_found" }))).toBe("This suggestion no longer exists")
    expect(describeComicsError(err(404, { code: "not_found" }))).toBe("That comics no longer exists")
    expect(describeCandidateError(err(409, { code: "image_not_addable", reason: "flagged", imageId: "i1" }), { i1: "x.jpg" }))
      .toBe('The image "x.jpg" is flagged')
    expect(describeCandidateError(err(409, { code: "image_not_addable", reason: "flagged", imageId: "zz" }), { i1: "x.jpg" }))
      .toBe("That image is flagged")
    expect(isCandidateGone(err(409, { code: "image_in_comics" }))).toBe(true)
    expect(isCandidateGone(err(422, { code: "subset_invalid" }))).toBe(false)
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

  it("explains popularity_conflict, keep_invalid and comics_source_flag", () => {
    expect(describeComicsError(err(409, { code: "popularity_conflict" })))
      .toBe("The popularity of a panel changed in the meantime, reload and try again")
    expect(describeComicsError(err(422, { code: "keep_invalid" })))
      .toBe("A panel marked to keep is not in this comics, reload and try again")
    expect(describeComicsError(err(409, { code: "comics_source_flag" })))
      .toBe("That image is a panel source of a built comics, its flag can't be changed")
  })

  it.each([
    ["too_few_panels", {}, "A comics needs at least two panels to be built"],
    ["panel_missing", {}, "A panel has no image, reload and try again"],
    ["image_not_active", {}, "A panel's image is not in the active library"],
    ["image_already_built", {}, "A panel's image is already a built comics"],
    ["same_as_existing", { imageId: "i9" }, "The built comics would be identical to an image already in the library"],
    ["other", {}, "The comics can't be built"],
  ])("explains not_buildable / %s", (reason, extra, text) => {
    expect(describeComicsError(err(409, { code: "not_buildable", reason, ...extra }))).toBe(text)
  })

  it.each([
    ["not_active", "Panel 2 is not in the active library"],
    ["flagged", "Panel 2 is flagged"],
    ["live_cut", "Panel 2 has a recut cut, use the cut instead"],
    ["file_missing", "The file of panel 2 is missing"],
    ["file_changed", "The file of panel 2 changed since it was added"],
    ["already_discarded", "Panel 2 was already merged into another meme"],
    ["weird", "Panel 2 can't be used"],
  ])("explains panel_unavailable / %s with the panel number", (cause, text) => {
    expect(describeComicsError(err(409, { code: "not_buildable", reason: "panel_unavailable", position: 2, cause }))).toBe(text)
  })

  it("explains panel_unavailable without a position", () => {
    expect(describeComicsError(err(409, { code: "not_buildable", reason: "panel_unavailable", cause: "flagged" })))
      .toBe("A panel is flagged")
  })

  it("has a generic fallback", () => {
    expect(describeComicsError(err(500))).toBe("Something went wrong, try again")
    expect(describeComicsError(new Error("x"))).toBe("Something went wrong, try again")
    expect(describeComicsError("nope")).toBe("Something went wrong, try again")
  })
})
