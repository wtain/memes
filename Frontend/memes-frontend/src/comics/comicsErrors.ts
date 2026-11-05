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
    case "built":
      return "This comics is built and can't be changed"
    case "not_found":
      return "That comics no longer exists"
    case "reorder_mismatch":
      return "The panels changed in the meantime, reload and try again"
    case "compose_failed":
      return describeCompose(detail.reason, detail.position)
    default:
      return GENERIC
  }
}
