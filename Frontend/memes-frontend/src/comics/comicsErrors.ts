import { ComicsRequestError } from "../api/errors"

const GENERIC = "Something went wrong, try again"

const NAME_REASON: Record<string, string> = {
  empty: "The name can't be empty",
  too_long: "The name is too long",
  forbidden_character: "The name contains a character that is not allowed",
  trailing_dot_or_space: "The name can't end with a dot or a space",
  reserved_name: "That name is reserved",
}

const NOT_ADDABLE_REASON: Record<string, string> = {
  not_active: "That image is not in the active library",
  flagged: "That image is flagged",
  live_cut: "That image has a recut cut, use the cut instead",
  not_found: "That image no longer exists",
}

function describeCompose(reason: unknown, position: unknown): string {
  const n = typeof position === "number" ? position : null
  switch (reason) {
    case "no_panels": return "The comics has no panels yet"
    case "too_large": return "The comics would be too large"
    case "unreadable": return n !== null ? `Panel ${n} can't be read` : "An image can't be read"
    case "unsupported_format": return `${n !== null ? `Panel ${n}` : "A panel"} has an unsupported image format`
    case "animated": return `${n !== null ? `Panel ${n}` : "A panel"} is animated`
    default: return n !== null ? `The comics can't be composed (panel ${n})` : "The comics can't be composed"
  }
}

function describeUnavailable(cause: unknown, position: unknown): string {
  const n = typeof position === "number" ? position : null
  const panel = n !== null ? `Panel ${n}` : "A panel"
  const file = n !== null ? `The file of panel ${n}` : "The file of a panel"
  switch (cause) {
    case "not_active": return `${panel} is not in the active library`
    case "flagged": return `${panel} is flagged`
    case "live_cut": return `${panel} has a recut cut, use the cut instead`
    case "file_missing": return `${file} is missing`
    case "file_changed": return `${file} changed since it was added`
    case "already_discarded": return `${panel} was already merged into another meme`
    default: return `${panel} can't be used`
  }
}

function describeNotBuildable(detail: Record<string, unknown>): string {
  switch (detail.reason) {
    case "too_few_panels": return "A comics needs at least two panels to be built"
    case "panel_missing": return "A panel has no image, reload and try again"
    case "image_not_active": return "A panel's image is not in the active library"
    case "image_already_built": return "A panel's image is already a built comics"
    case "same_as_existing": return "The built comics would be identical to an image already in the library"
    case "panel_unavailable": return describeUnavailable(detail.cause, detail.position)
    default: return "The comics can't be built"
  }
}

/** Error codes after which a suggestion is gone or ineligible on the server, so the page drops its row. */
export const CANDIDATE_GONE_CODES = [
  "candidate_decided", "not_found", "candidate_incomplete", "image_not_addable", "image_in_comics",
]

/** True when the failed suggestion request means the suggestion is no longer listed by the server. */
export function isCandidateGone(error: unknown): boolean {
  return error instanceof ComicsRequestError && error.code !== undefined && CANDIDATE_GONE_CODES.includes(error.code)
}

/**
 * The message for a failed request of the suggestions page: like describeComicsError, but a missing
 * suggestion is not "a comics", and an image refusal names the image by file name when the row has it.
 * `filenames` maps image id to file name for the row the request was about.
 */
export function describeCandidateError(error: unknown, filenames: Record<string, string> = {}): string {
  if (error instanceof ComicsRequestError) {
    if (error.code === "not_found") return "This suggestion no longer exists"
    if (error.code === "image_not_addable" || error.code === "image_in_comics") {
      const imageId = error.detail.imageId
      const name = typeof imageId === "string" ? filenames[imageId] : undefined
      const base = describeComicsError(error)
      if (name !== undefined) return base.replace(/^That image/, `The image "${name}"`)
      return base
    }
  }
  return describeComicsError(error)
}

/** A short plain-English message for a failed comics request. */
export function describeComicsError(error: unknown): string {
  if (!(error instanceof ComicsRequestError)) return GENERIC
  const { detail } = error
  switch (error.code) {
    case "name_invalid":
      return NAME_REASON[String(detail.reason)] ?? "That name is not allowed"
    case "name_taken":
      return "A comics with that name already exists"
    case "image_in_comics":
      return typeof detail.comicsName === "string"
        ? `That image is already a panel of the comics "${detail.comicsName}"`
        : "That image is already a panel of another comics"
    case "image_not_addable":
      return NOT_ADDABLE_REASON[String(detail.reason)] ?? "That image can't be added"
    case "candidate_decided":
      return "This suggestion was already decided, reload the page"
    case "candidate_incomplete":
      return "Some images of this suggestion no longer exist"
    case "subset_invalid":
      return detail.reason === "too_few_images"
        ? "Pick at least two images"
        : "The images changed in the meantime, reload the page"
    case "built":
      return "This comics is built and can't be changed"
    case "not_found":
      return "That comics no longer exists"
    case "reorder_mismatch":
      return "The panels changed in the meantime, reload and try again"
    case "popularity_conflict":
      return "The popularity of a panel changed in the meantime, reload and try again"
    case "keep_invalid":
      return "A panel marked to keep is not in this comics, reload and try again"
    case "comics_source_flag":
      return "That image is a panel source of a built comics, its flag can't be changed"
    case "not_buildable":
      return describeNotBuildable(detail)
    case "compose_failed":
      return describeCompose(detail.reason, detail.position)
    default:
      return GENERIC
  }
}
