import type { Meme } from "../types/generated/all"

const MAX_NAME = 120

export function extensionForMime(mime: string): "jpg" | "png" | "gif" | "webp" {
  switch (mime) {
    case "image/png":
      return "png"
    case "image/gif":
      return "gif"
    case "image/webp":
      return "webp"
    default:
      return "jpg"
  }
}

function sanitise(raw: string, mime: string): string {
  const last = raw.split(/[/\\]/).pop() ?? ""
  // eslint-disable-next-line no-control-regex
  let name = last.replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, "_").trim()
  if (!name) return ""
  const dot = name.lastIndexOf(".")
  if (dot <= 0 || dot === name.length - 1) {
    name = `${name.replace(/\.+$/, "")}.${extensionForMime(mime)}`
  }
  if (name.length > MAX_NAME) {
    const ext = name.slice(name.lastIndexOf("."))
    name = name.slice(0, MAX_NAME - ext.length) + ext
  }
  return name
}

export function buildFileName(meme: Pick<Meme, "id" | "originalFileName">, mime: string): string {
  const clean = meme.originalFileName ? sanitise(meme.originalFileName, mime) : ""
  return clean || `meme-${meme.id.slice(0, 8)}.${extensionForMime(mime)}`
}
