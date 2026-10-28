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
          <Route path="/admin" element={<div>admin-content</div>} />
          <Route path="/admin/profiles" element={<div>profiles-content</div>} />
          <Route path="/admin/recut" element={<div>recut-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

describe('AppLayout', () => {
  it('marks only Profiles as active on the profiles page, not Admin', () => {
    renderApp('/admin/profiles')
    expect(screen.getByText('Profiles')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('Admin')).not.toHaveAttribute('aria-current')
    expect(screen.getByText('Profiles')).toHaveAttribute('href', '/admin/profiles')
  })

  it('marks only Recut as active on the recut page, not Admin', () => {
    renderApp('/admin/recut')
    expect(screen.getByText('Recut')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('Admin')).not.toHaveAttribute('aria-current')
    expect(screen.getByText('Recut')).toHaveAttribute('href', '/admin/recut')
  })

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
