import { useState } from "react"
import { ComicsRequestError } from "../../api/errors"
import { describeComicsError } from "../../comics/comicsErrors"

type Props = { url: string }

/** An <img> cannot show the JSON error body of a failed preview, so on error the same URL is fetched once to read it. */
async function explainFailure(url: string): Promise<string> {
  try {
    const response = await fetch(url)
    if (response.ok) return "The preview could not be shown"
    let detail: Record<string, unknown> = {}
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (body.detail !== null && typeof body.detail === "object") detail = body.detail as Record<string, unknown>
    } catch {
      // not JSON: fall through with an empty detail
    }
    return describeComicsError(new ComicsRequestError(response.status, detail))
  } catch {
    return "The preview could not be loaded"
  }
}

export default function ComicsPreview({ url }: Props) {
  // The message is tagged with the URL that failed, so a newer preview never shows an older failure.
  const [failure, setFailure] = useState<{ url: string; message: string } | null>(null)
  const message = failure !== null && failure.url === url ? failure.message : null

  function onError() {
    void explainFailure(url).then((text) => setFailure({ url, message: text }))
  }

  return (
    <div>
      {message !== null && <p role="alert" className="mb-2">{message}</p>}
      {message === null && <img src={url} alt="Comics preview" onError={onError} className="max-w-full rounded border" />}
    </div>
  )
}
