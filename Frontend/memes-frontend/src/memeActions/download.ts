const REVOKE_DELAY_MS = 30_000

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.style.display = "none"
  document.body.appendChild(a)
  try {
    a.click()
  } finally {
    a.remove()
  }
  // Revoking at once breaks the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS)
}

/** Last-resort fallback when the image cannot be fetched as a blob. */
export function openInNewTab(url: string): void {
  window.open(url, "_blank", "noopener")
}
