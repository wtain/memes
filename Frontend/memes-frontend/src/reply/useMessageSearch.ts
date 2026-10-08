import { useCallback, useEffect, useReducer, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import { MessageSearchValidationError, isAbortError } from "../api/errors"
import { activeProfile, initialState, reducer } from "./messageSearchReducer"
import { toEditedProfile } from "./profile"
import { buildRequest } from "./requests"
import type { Mode, Overrides } from "./requests"
import { browserStorage, loadSaved, save } from "./storage"
import type { AxisName, Filters, QueryProfile } from "./types"
import { DEBOUNCE_MS, EMPTY_FILTERS } from "./types"

type Options = { debounceMs?: number; storage?: Storage | null }

export function useMessageSearch(memesApi: MemesApi, options: Options = {}) {
  const debounceMs = options.debounceMs ?? DEBOUNCE_MS
  const [storage] = useState<Storage | null>(() => (options.storage === undefined ? browserStorage() : options.storage))
  const [saved] = useState(() => loadSaved(storage))
  const [state, dispatch] = useReducer(reducer, saved, initialState)

  const stateRef = useRef(state)
  const seqRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    stateRef.current = state
  })

  const execute = useCallback(async (mode: Mode, overrides: Overrides = {}) => {
    const current = stateRef.current
    if (mode === "more" && (!current.lastRequest || !current.nextCursor)) return
    const request = buildRequest(current, mode, overrides)
    const seq = ++seqRef.current
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    if (mode === "find") dispatch({ type: "findStarted", submitted: request.text })
    else if (mode === "refine") dispatch({ type: "refineStarted" })
    else dispatch({ type: "moreStarted" })

    try {
      const response = await memesApi.searchMemeFromText(request, controller.signal)
      if (seq !== seqRef.current) return
      dispatch({ type: "succeeded", mode, response, request })
    } catch (error) {
      if (isAbortError(error) || seq !== seqRef.current) return
      dispatch({ type: "failed", error: error instanceof MessageSearchValidationError ? "validation" : "failed" })
    }
  }, [memesApi])

  function cancelTimer() {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  // Restore: a saved search re-runs retrieval only (the saved reading is sent back). The dispatch happens after an
  // await so it is not a synchronous state update in the effect body.
  useEffect(() => {
    if (!saved?.submitted) return
    void Promise.resolve().then(() => execute("refine"))
  }, [saved, execute])

  useEffect(() => {
    save(storage, {
      text: state.text, submitted: state.submitted, originalProfile: state.originalProfile,
      editedProfile: state.editedProfile, filters: state.filters,
    })
  }, [storage, state.text, state.submitted, state.originalProfile, state.editedProfile, state.filters])

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    abortRef.current?.abort()
  }, [])

  function setText(text: string) {
    dispatch({ type: "text", text })
  }

  function find() {
    if (stateRef.current.text.trim() === "") return
    cancelTimer()
    void execute("find")
  }

  // "Try again": keeps the current reading, edits and filters when there is a reading; otherwise re-sends the text.
  function retry() {
    const current = stateRef.current
    if (current.submitted && (current.originalProfile || current.editedProfile)) {
      cancelTimer()
      void execute("refine")
      return
    }
    find()
  }

  function editProfile(update: (profile: QueryProfile) => QueryProfile) {
    const current = stateRef.current
    const base = activeProfile(current)
    if (!base) return
    const updated = update(base)
    if (updated === base) return
    const next = toEditedProfile(updated)
    dispatch({ type: "edited", profile: next })
    cancelTimer()
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      void execute("refine", { editedProfile: next })
    }, debounceMs)
  }

  function resetProfile() {
    if (!stateRef.current.editedProfile) return
    cancelTimer()
    dispatch({ type: "resetEdit" })
    void execute("refine", { editedProfile: null })
  }

  function setFilter(axis: AxisName, values: string[]) {
    const current = stateRef.current
    if (!current.submitted) return
    const filters: Filters = { ...current.filters, [axis]: values }
    cancelTimer()
    dispatch({ type: "filters", filters })
    void execute("refine", { filters })
  }

  function clearFilters() {
    const current = stateRef.current
    if (!current.submitted) return
    cancelTimer()
    dispatch({ type: "filters", filters: { ...EMPTY_FILTERS } })
    void execute("refine", { filters: { ...EMPTY_FILTERS } })
  }

  function loadMore() {
    const current = stateRef.current
    if (!current.hasNext || !current.nextCursor || current.loadingMore) return
    // The list is about to change (a refine is running or an edit is waiting to be sent): its cursor is stale.
    if (current.status === "reading" || current.status === "refining" || timerRef.current !== null) return
    void execute("more")
  }

  return {
    state, activeProfile: activeProfile(state),
    setText, find, retry, editProfile, resetProfile, setFilter, clearFilters, loadMore,
  }
}
