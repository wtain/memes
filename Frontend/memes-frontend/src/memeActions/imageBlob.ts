export type ImageActionReason = "network" | "convert" | "too_large"

export class ImageActionError extends Error {
  reason: ImageActionReason
  constructor(reason: ImageActionReason) {
    super(reason)
    this.name = "ImageActionError"
    this.reason = reason
  }
}

const MAX_CACHE = 8
const MAX_PIXELS = 16_000_000

export async function fetchImageBlob(url: string): Promise<Blob> {
  let response: Response
  try {
    response = await fetch(url, { mode: "cors", credentials: "omit" })
  } catch {
    throw new ImageActionError("network")
  }
  if (!response.ok) throw new ImageActionError("network")
  try {
    return await response.blob()
  } catch {
    throw new ImageActionError("network")
  }
}

const cache = new Map<string, Promise<Blob>>()

export function warmImageBlob(id: string, url: string): Promise<Blob> {
  const hit = cache.get(id)
  if (hit) return hit
  const promise = fetchImageBlob(url)
  cache.set(id, promise)
  promise.catch(() => {
    if (cache.get(id) === promise) cache.delete(id)
  })
  while (cache.size > MAX_CACHE) {
    const oldest = cache.keys().next().value
    if (oldest === undefined) break
    cache.delete(oldest)
  }
  return promise
}

export function _resetImageBlobCacheForTests(): void {
  cache.clear()
}

/** True for a GIF with more than one Graphic Control Extension (0x21 0xF9). */
export function isAnimatedGif(bytes: Uint8Array): boolean {
  if (
    bytes.length < 6 ||
    bytes[0] !== 0x47 ||
    bytes[1] !== 0x49 ||
    bytes[2] !== 0x46 ||
    bytes[3] !== 0x38
  ) {
    return false
  }
  let frames = 0
  for (let i = 6; i < bytes.length - 1; i++) {
    if (bytes[i] === 0x21 && bytes[i + 1] === 0xf9) {
      frames++
      if (frames > 1) return true
    }
  }
  return false
}

type Decoded = { source: CanvasImageSource; width: number; height: number; release: () => void }

async function decode(blob: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(blob)
    return {
      source: bitmap,
      width: bitmap.width,
      height: bitmap.height,
      release: () => bitmap.close(),
    }
  }
  const objectUrl = URL.createObjectURL(blob)
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error("decode"))
      el.src = objectUrl
    })
    return {
      source: img,
      width: img.naturalWidth,
      height: img.naturalHeight,
      release: () => {},
    }
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

export async function toPngBlob(blob: Blob): Promise<Blob> {
  if (blob.type === "image/png") return blob
  let decoded: Decoded
  try {
    decoded = await decode(blob)
  } catch {
    throw new ImageActionError("convert")
  }
  try {
    if (decoded.width * decoded.height > MAX_PIXELS) throw new ImageActionError("too_large")
    const canvas = document.createElement("canvas")
    canvas.width = decoded.width
    canvas.height = decoded.height
    const ctx = canvas.getContext("2d")
    if (!ctx) throw new ImageActionError("convert")
    ctx.drawImage(decoded.source, 0, 0)
    const png = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"))
    if (!png) throw new ImageActionError("convert")
    return png
  } catch (e) {
    if (e instanceof ImageActionError) throw e
    throw new ImageActionError("convert")
  } finally {
    decoded.release()
  }
}
