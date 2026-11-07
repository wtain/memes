const MAX_NAME = 80
const PREFIX = "Comics "
// Mirrors repository/comics_name.py: <>:"/\|?* and control characters are not allowed.
// eslint-disable-next-line no-control-regex
const FORBIDDEN = /[<>:"/\\|?*\u0000-\u001f\u007f]/g
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i

/** The first UUID anywhere in `text` (a bare id or a pasted details URL), lower-cased; null when none. */
export function extractUuid(text: string): string | null {
  const m = UUID.exec(text)
  return m === null ? null : m[0].toLowerCase()
}

function cleanEnd(s: string): string {
  return s.replace(/[. ]+$/, "")
}

/**
 * The default name of a draft started from one image: `Comics <file name without extension>`,
 * or `Comics <first 8 characters of the id>` when nothing usable is left. `withIdSuffix` appends
 * the id's first 8 characters (the retry after a name conflict). Never longer than 80 characters.
 */
export function defaultComicsName(fileName: string | null | undefined, imageId: string, withIdSuffix = false): string {
  const short = imageId.slice(0, 8)
  const file = fileName ?? ""
  const dot = file.lastIndexOf(".")
  const base = dot > 0 ? file.slice(0, dot) : file
  const stem = base.replace(FORBIDDEN, "").replace(/\s+/g, " ").trim()
  const suffix = withIdSuffix ? ` ${short}` : ""
  const room = MAX_NAME - PREFIX.length - suffix.length
  const body = stem === "" ? short : cleanEnd(stem.slice(0, room).trim())
  return `${PREFIX}${body === "" ? short : body}${suffix}`
}
