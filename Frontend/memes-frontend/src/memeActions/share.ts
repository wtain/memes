export type ShareResult = { kind: "shared" } | { kind: "cancelled" } | { kind: "error" }

export function canShareFiles(): boolean {
  return (
    typeof navigator !== "undefined" &&
    typeof navigator.share === "function" &&
    typeof navigator.canShare === "function"
  )
}

export async function shareImage(blob: Blob, filename: string): Promise<ShareResult> {
  try {
    const file = new File([blob], filename, { type: blob.type })
    if (!navigator.canShare({ files: [file] })) return { kind: "error" }
    await navigator.share({ files: [file] })
    return { kind: "shared" }
  } catch (e) {
    if (typeof e === "object" && e !== null && (e as { name?: unknown }).name === "AbortError") {
      return { kind: "cancelled" }
    }
    return { kind: "error" }
  }
}
