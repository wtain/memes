export type CopyResult = { kind: "copied" } | { kind: "error"; reason: "blocked" | "unsupported" }

export function canCopyImage(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof ClipboardItem !== "undefined" &&
    !!navigator.clipboard?.write
  )
}

function isNotAllowed(e: unknown): boolean {
  return typeof e === "object" && e !== null && (e as { name?: unknown }).name === "NotAllowedError"
}

function execCommandCopy(text: string): boolean {
  const area = document.createElement("textarea")
  area.value = text
  area.setAttribute("readonly", "")
  area.style.position = "fixed"
  area.style.top = "0"
  area.style.left = "-9999px"
  area.style.opacity = "0"
  document.body.appendChild(area)
  try {
    area.select()
    return typeof document.execCommand === "function" && document.execCommand("copy")
  } catch {
    return false
  } finally {
    area.remove()
  }
}

export async function copyText(text: string): Promise<CopyResult> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return { kind: "copied" }
    } catch (e) {
      if (isNotAllowed(e)) return { kind: "error", reason: "blocked" }
      // fall through to the legacy path
    }
  }
  return execCommandCopy(text)
    ? { kind: "copied" }
    : { kind: "error", reason: "unsupported" }
}

/**
 * Safari contract: the ClipboardItem is built and `write` is called synchronously
 * (no await before it), with the blob as a Promise. A rejection of the blob promise
 * itself is rethrown unchanged so the caller can map its own error reasons.
 */
export async function writeImageToClipboard(blob: Promise<Blob>): Promise<CopyResult> {
  try {
    const item = new ClipboardItem({ "image/png": blob })
    await navigator.clipboard.write([item])
    return { kind: "copied" }
  } catch (e) {
    if (isNotAllowed(e)) return { kind: "error", reason: "blocked" }
    throw e
  }
}
