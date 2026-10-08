/** The server rejected the request (HTTP 422). `details` holds the server's messages only (never the input). */
export class MessageSearchValidationError extends Error {
  readonly details: string[]
  constructor(details: string[]) {
    super("The message search request was rejected")
    this.name = "MessageSearchValidationError"
    this.details = details
  }
}

/** Any other failure of the message search. The message is fixed text: it never contains the user's message. */
export class MessageSearchFailedError extends Error {
  readonly status: number
  constructor(status: number) {
    super(`Message search failed (HTTP ${status})`)
    this.name = "MessageSearchFailedError"
    this.status = status
  }
}

export function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") return true
  if (error && typeof error === "object") {
    const e = error as Record<string, unknown>
    return e.name === "AbortError"
  }
  return false
}
