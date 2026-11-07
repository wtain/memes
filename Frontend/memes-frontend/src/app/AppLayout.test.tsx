import { render, screen, fireEvent, within } from '@testing-library/react'
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
          <Route path="/admin/comics/:id" element={<div>comics-editor</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

const GROUPS: Record<string, string[]> = {
  Find: ['Search', 'Reply', 'Recommendations', 'Explore', 'Trends', 'Trending memes'],
  Library: ['Upload', 'Concepts', 'Statistics'],
  Cleanup: ['Untagged', 'No OCR', 'Flagged', 'Duplicates', 'Ingestion'],
  Admin: ['Batches', 'Profiles', 'Recut', 'Comics', 'Discovery'],
}

function open(group: string) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${group}`) }))
}

describe('AppLayout menu', () => {
  beforeEach(() => {
    exploreMountCount = 0
  })

  it('shows four category buttons and no page links while all menus are closed', () => {
    renderApp('/explore')
    for (const group of Object.keys(GROUPS)) {
      expect(screen.getByRole('button', { name: new RegExp(`^${group}`) })).toHaveAttribute('aria-expanded', 'false')
    }
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it.each(Object.entries(GROUPS))('the %s menu lists its links in order', (group, labels) => {
    renderApp('/explore')
    open(group)
    const menu = screen.getByRole('menu', { name: group })
    expect(within(menu).getAllByRole('menuitem').map(a => a.textContent)).toEqual(labels)
  })

  it('separates Trends from the other Find links', () => {
    renderApp('/explore')
    open('Find')
    const menu = screen.getByRole('menu', { name: 'Find' })
    const children = Array.from(menu.children)
    const sepAt = children.findIndex(li => li.getAttribute('role') === 'separator')
    expect(sepAt).toBeGreaterThan(0)
    expect(children[sepAt - 1]).toHaveTextContent('Explore')
    expect(children[sepAt + 1]).toHaveTextContent('Trends')
  })

  it('links to the right routes', () => {
    renderApp('/explore')
    open('Admin')
    expect(screen.getByRole('menuitem', { name: 'Batches' })).toHaveAttribute('href', '/admin')
    expect(screen.getByRole('menuitem', { name: 'Profiles' })).toHaveAttribute('href', '/admin/profiles')
  })

  it('opens one menu at a time', () => {
    renderApp('/explore')
    open('Find')
    open('Cleanup')
    expect(screen.queryByRole('menu', { name: 'Find' })).not.toBeInTheDocument()
    expect(screen.getByRole('menu', { name: 'Cleanup' })).toBeInTheDocument()
  })

  it('closes on Escape, on an outside click and after choosing a link', () => {
    renderApp('/explore')
    open('Cleanup')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()

    open('Cleanup')
    fireEvent.mouseDown(screen.getByText('explore-content'))
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()

    open('Cleanup')
    fireEvent.click(screen.getByRole('menuitem', { name: 'Untagged' }))
    expect(screen.getByText('untagged-content')).toBeInTheDocument()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('highlights the category of the current page', () => {
    renderApp('/untagged')
    expect(screen.getByRole('button', { name: /^Cleanup/ })).toHaveAttribute('data-active', 'true')
    expect(screen.getByRole('button', { name: /^Find/ })).not.toHaveAttribute('data-active')
  })

  it('keeps the Admin category highlighted on the comics editor page', () => {
    renderApp('/admin/comics/c1')
    expect(screen.getByRole('button', { name: /^Admin/ })).toHaveAttribute('data-active', 'true')
  })

  it('marks only Profiles as active on the profiles page, not Batches', () => {
    renderApp('/admin/profiles')
    open('Admin')
    expect(screen.getByRole('menuitem', { name: 'Profiles' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('menuitem', { name: 'Batches' })).not.toHaveAttribute('aria-current')
  })

  it('marks only Recut as active on the recut page, not Batches', () => {
    renderApp('/admin/recut')
    open('Admin')
    expect(screen.getByRole('menuitem', { name: 'Recut' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('menuitem', { name: 'Batches' })).not.toHaveAttribute('aria-current')
  })

  it('remounts the current page when re-clicking its own already-active menu link (regression: NavLink to the current route is a no-op in React Router, so scroll/pagination state was otherwise unreachable without a full page refresh)', () => {
    renderApp('/explore')
    expect(exploreMountCount).toBe(1)
    open('Find')
    fireEvent.click(screen.getByRole('menuitem', { name: 'Explore' }))
    expect(exploreMountCount).toBe(2)
  })

  it('does not remount the current page when navigating to a different page', () => {
    renderApp('/explore')
    expect(exploreMountCount).toBe(1)
    open('Cleanup')
    fireEvent.click(screen.getByRole('menuitem', { name: 'Untagged' }))
    expect(screen.getByText('untagged-content')).toBeInTheDocument()
    expect(exploreMountCount).toBe(1)
  })
})
