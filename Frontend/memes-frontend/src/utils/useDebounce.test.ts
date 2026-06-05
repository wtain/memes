import { renderHook, act } from '@testing-library/react'
import { useDebounce } from './useDebounce'

describe('useDebounce', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns the initial value immediately', () => {
    const { result } = renderHook(() => useDebounce('hello', 300))
    expect(result.current[0]).toBe('hello')
  })

  it('does not update value before delay elapses', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useDebounce(value, 300),
      { initialProps: { value: 'hello' } }
    )
    rerender({ value: 'world' })
    act(() => { vi.advanceTimersByTime(299) })
    expect(result.current[0]).toBe('hello')
  })

  it('updates debounced value after delay elapses', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useDebounce(value, 300),
      { initialProps: { value: 'hello' } }
    )
    rerender({ value: 'world' })
    act(() => { vi.advanceTimersByTime(300) })
    expect(result.current[0]).toBe('world')
  })

  it('resets the timer when value changes rapidly', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useDebounce(value, 300),
      { initialProps: { value: 'a' } }
    )
    rerender({ value: 'ab' })
    act(() => { vi.advanceTimersByTime(200) })
    rerender({ value: 'abc' })
    act(() => { vi.advanceTimersByTime(200) })
    expect(result.current[0]).toBe('a')

    act(() => { vi.advanceTimersByTime(100) })
    expect(result.current[0]).toBe('abc')
  })

  it('exposes a setter that updates the debounced value immediately', () => {
    const { result } = renderHook(() => useDebounce('initial', 300))
    act(() => { result.current[1]('direct') })
    expect(result.current[0]).toBe('direct')
  })
})
