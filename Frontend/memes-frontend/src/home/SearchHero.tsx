import { useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"
import { Button, Skeleton, buttonClasses } from "../ui"
import { pickExampleCandidates, readCachedExamples, verifyExamples } from "./examples"
import { clearRecentSearches, readRecentSearches, rememberSearch, shortenForChip } from "./recentSearches"

type Props = {
  memesApi: MemesApi
  /** The memes the rails fetched; null while they load. Example searches are derived from their tags. */
  candidatesSource: Meme[] | null
}

const CHIP = "rounded-full"

function searchPath(query: string): string {
  return `/search?q=${encodeURIComponent(query)}`
}

export function SearchHero({ memesApi, candidatesSource }: Props) {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [recent, setRecent] = useState(readRecentSearches)
  const [cached] = useState(readCachedExamples)
  const [verified, setVerified] = useState<string[] | null>(null)

  // A string key keeps the effect from restarting each time the parent builds a new array.
  const candidateKey = candidatesSource === null ? null : pickExampleCandidates(candidatesSource).join(" ")

  useEffect(() => {
    // Examples are not shown beside the last searches, and a finished verification (cached or not) is final.
    if (recent.length > 0 || cached !== null || verified !== null || candidateKey === null) return
    const controller = new AbortController()
    const candidates = candidateKey === "" ? [] : candidateKey.split(" ")
    verifyExamples(memesApi, candidates, controller.signal).then(found => {
      if (!controller.signal.aborted) setVerified(found)
    })
    return () => controller.abort()
  }, [memesApi, cached, candidateKey, recent.length, verified])

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return
    if (window.matchMedia("(pointer: fine)").matches) inputRef.current?.focus()
  }, [])

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = new FormData(event.currentTarget).get("q")
    const query = typeof value === "string" ? value.trim() : ""
    if (query === "") {
      navigate("/search")
      return
    }
    rememberSearch(query)
    navigate(searchPath(query))
  }

  function onClear() {
    clearRecentSearches()
    setRecent([])
  }

  const examples = cached ?? verified
  let chips: React.ReactNode = null
  if (recent.length > 0) {
    chips = (
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-sm text-fg-muted">Your last searches</span>
        {recent.map(query => (
          <Link
            key={query}
            to={searchPath(query)}
            title={query}
            aria-label={query}
            className={buttonClasses({ size: "lg", className: CHIP })}
          >
            {shortenForChip(query)}
          </Link>
        ))}
        <Button variant="ghost" size="lg" onClick={onClear}>
          Clear
        </Button>
      </div>
    )
  } else if (examples === null) {
    chips = (
      <div data-testid="chips-skeleton" aria-hidden="true" className="mt-3 flex h-11 items-center gap-2">
        <Skeleton className="h-11 w-20 rounded-full" />
        <Skeleton className="h-11 w-24 rounded-full" />
        <Skeleton className="h-11 w-16 rounded-full" />
      </div>
    )
  } else if (examples.length > 0) {
    chips = (
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-sm text-fg-muted">Try:</span>
        {examples.map(word => (
          <Link key={word} to={searchPath(word)} className={buttonClasses({ size: "lg", className: CHIP })}>
            {word}
          </Link>
        ))}
      </div>
    )
  }

  return (
    <section className="pt-8 sm:pt-12">
      <h1 className="text-3xl font-semibold text-fg sm:text-4xl">Find the right meme for any moment</h1>
      <p className="mt-2 text-base text-fg-muted">
        Search the meme library by the words on a meme, what it shows, or a feeling.
      </p>
      <form role="search" onSubmit={onSubmit} className="mt-6 flex flex-col gap-2 sm:flex-row">
        <input
          ref={inputRef}
          type="search"
          name="q"
          aria-label="Search memes"
          placeholder="Search memes..."
          autoComplete="off"
          className="min-h-12 w-full flex-1 rounded-md bg-surface px-4 text-base text-fg ring-1 ring-inset ring-line-strong placeholder:text-fg-subtle"
        />
        <Button type="submit" variant="primary" size="lg" className="min-h-12">
          Search
        </Button>
      </form>
      {chips}
    </section>
  )
}
