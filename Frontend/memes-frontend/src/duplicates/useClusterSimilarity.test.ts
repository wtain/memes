import { act, renderHook, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { makeMockApi } from '../test/mockApi'
import { makeSimilarityPair, makeSimilarityResponse } from './fixtures'
import { useClusterSimilarity } from './useClusterSimilarity'

describe('useClusterSimilarity', () => {
  it('starts empty and fetches nothing on mount', () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useClusterSimilarity(api), { wrapper: StrictMode })
    expect(result.current.entries.size).toBe(0)
    expect(api.getClusterSimilarity).not.toHaveBeenCalled()
  })

  it('load marks the cluster loading, then ok with the response', async () => {
    const response = makeSimilarityResponse({ pairs: [makeSimilarityPair()] })
    const api = makeMockApi({ getClusterSimilarity: vi.fn().mockResolvedValue(response) })
    const { result } = renderHook(() => useClusterSimilarity(api))

    act(() => result.current.load(7))
    expect(result.current.entries.get(7)).toEqual({ status: 'loading' })
    await waitFor(() => expect(result.current.entries.get(7)).toEqual({ status: 'ok', pairs: response.pairs, capped: false }))
    expect(api.getClusterSimilarity).toHaveBeenCalledWith(7)
  })

  it('requests a cluster only once however often load is called', async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useClusterSimilarity(api), { wrapper: StrictMode })

    act(() => { result.current.load(7); result.current.load(7) })
    act(() => result.current.load(7))

    await waitFor(() => expect(result.current.entries.get(7)?.status).toBe('ok'))
    expect(api.getClusterSimilarity).toHaveBeenCalledTimes(1)
  })

  it('keeps the capped flag', async () => {
    const api = makeMockApi({ getClusterSimilarity: vi.fn().mockResolvedValue(makeSimilarityResponse({ capped: true })) })
    const { result } = renderHook(() => useClusterSimilarity(api))
    act(() => result.current.load(3))
    await waitFor(() => expect(result.current.entries.get(3)).toEqual({ status: 'ok', pairs: [], capped: true }))
  })

  it('a failed fetch ends in error and retry fetches again', async () => {
    const getClusterSimilarity = vi.fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(makeSimilarityResponse())
    const api = makeMockApi({ getClusterSimilarity })
    const { result } = renderHook(() => useClusterSimilarity(api))

    act(() => result.current.load(5))
    await waitFor(() => expect(result.current.entries.get(5)).toEqual({ status: 'error' }))

    act(() => result.current.retry(5))
    expect(result.current.entries.get(5)).toEqual({ status: 'loading' })
    await waitFor(() => expect(result.current.entries.get(5)?.status).toBe('ok'))
    expect(getClusterSimilarity).toHaveBeenCalledTimes(2)
  })

  it('keeps clusters independent', async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useClusterSimilarity(api))
    act(() => { result.current.load(1); result.current.load(2) })
    await waitFor(() => expect(result.current.entries.get(2)?.status).toBe('ok'))
    expect(result.current.entries.get(1)?.status).toBe('ok')
    expect(api.getClusterSimilarity).toHaveBeenCalledTimes(2)
  })
})
