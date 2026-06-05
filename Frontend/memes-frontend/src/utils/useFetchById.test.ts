import { renderHook, act } from '@testing-library/react'
import { StrictMode } from 'react'
import { useFetchById } from './useFetchById'

describe('useFetchById', () => {
  it('calls fetcher with the id and passes result to onResult', async () => {
    const fetcher = vi.fn().mockResolvedValue('data')
    const onResult = vi.fn()
    renderHook(() => useFetchById('id-1', fetcher, onResult))
    await act(async () => {})
    expect(fetcher).toHaveBeenCalledWith('id-1')
    expect(onResult).toHaveBeenCalledWith('data')
  })

  it('does not call fetcher when id is undefined', async () => {
    const fetcher = vi.fn().mockResolvedValue('data')
    renderHook(() => useFetchById(undefined, fetcher, vi.fn()))
    await act(async () => {})
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('calls fetcher once per mount in production (no StrictMode)', async () => {
    const fetcher = vi.fn().mockResolvedValue('data')
    renderHook(() => useFetchById('id-1', fetcher, vi.fn()))
    await act(async () => {})
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('in StrictMode, fetcher fires once and onResult is called once', async () => {
    // StrictMode double-invokes effects (mount → cleanup → remount).
    // The ref-guard skips the second fetch for the same ID, so only one request goes out.
    const fetcher = vi.fn().mockResolvedValue('data')
    const onResult = vi.fn()
    renderHook(() => useFetchById('id-1', fetcher, onResult), { wrapper: StrictMode })
    await act(async () => {})
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(onResult).toHaveBeenCalledTimes(1)
    expect(onResult).toHaveBeenCalledWith('data')
  })

  it('re-fetches when id changes and discards the stale result', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce('result-A')
      .mockResolvedValueOnce('result-B')
    const onResult = vi.fn()
    let id = 'id-A'
    const { rerender } = renderHook(() => useFetchById(id, fetcher, onResult))
    await act(async () => {})
    expect(onResult).toHaveBeenLastCalledWith('result-A')

    id = 'id-B'
    rerender()
    await act(async () => {})
    expect(onResult).toHaveBeenLastCalledWith('result-B')
    expect(onResult).toHaveBeenCalledTimes(2)
  })

  it('calls onError when fetcher rejects', async () => {
    const err = new Error('boom')
    const fetcher = vi.fn().mockRejectedValue(err)
    const onError = vi.fn()
    renderHook(() => useFetchById('id-1', fetcher, vi.fn(), onError))
    await act(async () => {})
    expect(onError).toHaveBeenCalledWith(err)
  })

  it('does not call onResult after id changes (stale response suppressed)', async () => {
    let resolveA!: (v: string) => void
    const promiseA = new Promise<string>(r => { resolveA = r })
    const fetcher = vi.fn()
      .mockReturnValueOnce(promiseA)
      .mockResolvedValueOnce('result-B')
    const onResult = vi.fn()
    let id = 'id-A'
    const { rerender } = renderHook(() => useFetchById(id, fetcher, onResult))

    // Change to id-B before A resolves
    id = 'id-B'
    rerender()
    await act(async () => {})
    expect(onResult).toHaveBeenCalledWith('result-B')

    // Now resolve the stale A promise
    await act(async () => { resolveA('result-A') })
    expect(onResult).not.toHaveBeenCalledWith('result-A')
    expect(onResult).toHaveBeenCalledTimes(1)
  })
})
