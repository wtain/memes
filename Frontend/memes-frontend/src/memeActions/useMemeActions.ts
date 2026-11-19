import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"
import { useToast, type ToastTone } from "../ui"
import { canCopyImage, copyText, writeImageToClipboard, type CopyResult } from "./clipboard"
import { downloadBlob, openInNewTab } from "./download"
import { buildFileName } from "./filename"
import { ImageActionError, isAnimatedGif, toPngBlob, warmImageBlob } from "./imageBlob"
import { memePageUrl } from "./links"
import { canShareFiles, shareImage } from "./share"

export type ActionKind = "copyImage" | "copyLink" | "download" | "share"

const DONE_MS = 1500

const TEXT = {
  imageCopied: "Image copied",
  linkCopied: "Link copied",
  noImageCopy: "Image copy needs HTTPS or a newer browser. Link copied instead.",
  animatedGif:
    "Animated GIF cannot be copied as an image. Image link copied; use Download or Share to keep the animation.",
  animatedGifFailed: "Animated GIFs cannot be copied as an image. Use Download or Copy link.",
  blocked: "Copy was blocked by the browser. Try again or use Download.",
  network: "Could not load the image to copy.",
  convert: "Could not convert the image. Use Download.",
  tooLarge: "Too large to copy. Use Download.",
  unsupported: "Could not copy. Use Download, or copy the link from the address bar.",
  downloadStarted: "Download started",
  downloadFallback: "Could not download directly; the image opened in a new tab.",
  shareFailed: "Could not share.",
} as const

class AnimatedGifSignal extends Error {}

export function useMemeActions(meme: Meme, memesApi: MemesApi) {
  const toast = useToast()
  const [busy, setBusy] = useState<ActionKind | null>(null)
  const [done, setDone] = useState<ActionKind | null>(null)
  const [message, setMessage] = useState("")
  const inFlight = useRef(false)

  useEffect(() => {
    if (done === null) return
    const timer = setTimeout(() => setDone(null), DONE_MS)
    return () => clearTimeout(timer)
  }, [done])

  function report(text: string, tone: ToastTone) {
    setMessage(text)
    toast.show(text, tone)
  }

  function reportCopy(kind: ActionKind, result: CopyResult, okText: string, okTone: ToastTone) {
    if (result.kind === "copied") {
      report(okText, okTone)
      setDone(kind)
    } else {
      report(result.reason === "blocked" ? TEXT.blocked : TEXT.unsupported, "error")
    }
  }

  async function copyImage() {
    if (!canCopyImage()) {
      reportCopy("copyImage", await copyText(memePageUrl(meme.id)), TEXT.noImageCopy, "info")
      return
    }
    // Safari: build the item and call write() with no await before it.
    const pngPromise = warmImageBlob(meme.id, memesApi.getImageUrl(meme)).then(async (blob) => {
      if (blob.type === "image/gif" && isAnimatedGif(new Uint8Array(await blob.arrayBuffer()))) {
        throw new AnimatedGifSignal()
      }
      return toPngBlob(blob)
    })
    try {
      reportCopy("copyImage", await writeImageToClipboard(pngPromise), TEXT.imageCopied, "success")
    } catch (e) {
      if (e instanceof AnimatedGifSignal) {
        // This copy runs after awaited fetch work, so the click's user activation may have
        // lapsed; never report a generic failure or a success it cannot back up.
        const linkResult = await copyText(memesApi.getImageUrl(meme))
        if (linkResult.kind === "copied") {
          report(TEXT.animatedGif, "info")
          setDone("copyImage")
        } else {
          report(TEXT.animatedGifFailed, "error")
        }
      } else if (e instanceof ImageActionError) {
        report(e.reason === "network" ? TEXT.network : e.reason === "too_large" ? TEXT.tooLarge : TEXT.convert, "error")
      } else {
        report(TEXT.unsupported, "error")
      }
    }
  }

  async function download() {
    const url = memesApi.getImageUrl(meme)
    try {
      const blob = await warmImageBlob(meme.id, url)
      downloadBlob(blob, buildFileName(meme, blob.type))
      report(TEXT.downloadStarted, "success")
      setDone("download")
    } catch {
      openInNewTab(url)
      report(TEXT.downloadFallback, "info")
    }
  }

  async function share() {
    let blob: Blob
    try {
      blob = await warmImageBlob(meme.id, memesApi.getImageUrl(meme))
    } catch {
      report(TEXT.network, "error")
      return
    }
    const result = await shareImage(blob, buildFileName(meme, blob.type))
    if (result.kind === "shared") setDone("share")
    else if (result.kind === "error") report(TEXT.shareFailed, "error")
  }

  async function execute(kind: ActionKind) {
    switch (kind) {
      case "copyImage":
        return copyImage()
      case "copyLink":
        return reportCopy("copyLink", await copyText(memePageUrl(meme.id)), TEXT.linkCopied, "success")
      case "download":
        return download()
      case "share":
        return share()
    }
  }

  function run(kind: ActionKind) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(kind)
    execute(kind)
      .catch(() => report(TEXT.unsupported, "error"))
      .finally(() => {
        inFlight.current = false
        setBusy(null)
      })
  }

  function warm() {
    warmImageBlob(meme.id, memesApi.getImageUrl(meme)).catch(() => {})
  }

  return { run, busy, done, message, warm, canShare: canShareFiles() }
}
