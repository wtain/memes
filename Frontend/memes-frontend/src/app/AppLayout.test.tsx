import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import AppLayout from './AppLayout'

let exploreMountCount = 0
function DummyExplore() {
  exploreMountCount++
  return <div>explore-content</div>
}
function DummyUntagged() {
  return <div>untagged-content</div>
}

function renderApp(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/explore" element={<DummyExplore />} />
          <Route path="/untagged" element={<DummyUntagged />} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

describe('AppLayout', () => {
  beforeEach(() => {
    exploreMountCount = 0
  })

  it('remounts the current page when re-clicking its own already-active nav link (regression: NavLink to the current route is a no-op in React Router, so scroll/pagination state was otherwise unreachable without a full page refresh)', () => {
    renderApp('/explore')
    expect(exploreMountCount).toBe(1)

    fireEvent.click(screen.getByText('Explore'))

    expect(exploreMountCount).toBe(2)
  })

  it('does not remount the current page when navigating to a different page', () => {
    renderApp('/explore')
    expect(exploreMountCount).toBe(1)

    fireEvent.click(screen.getByText('Untagged'))

    expect(screen.getByText('untagged-content')).toBeInTheDocument()
    expect(exploreMountCount).toBe(1) // unchanged -- this was a normal navigation away, not a reset
  })
})
