import { useEffect, useRef, useState } from "react"
import { Outlet, NavLink, matchPath, useLocation, useNavigate } from "react-router-dom"

// handleListNavClick below is wired up on the NavLinks marked `list` in MENU -- /explore,
// /untagged, /duplicates, /no-ocr, /flagged, /search, /recommendations -- the pages backed by
// MemesList/MemesDuplicatesList's windowed infinite scroll. React Router doesn't navigate (or
// remount anything) when you click a NavLink to the route you're already on, so without this,
// scroll position/pagination state on these pages was otherwise unreachable except via a full
// page refresh.

type MenuLink = { to: string; label: string; end?: boolean; list?: boolean }
type MenuEntry = MenuLink | "separator"
type MenuGroup = { label: string; entries: MenuEntry[] }

const MENU: MenuGroup[] = [
  {
    label: "Find",
    entries: [
      { to: "/search", label: "Search", list: true },
      { to: "/reply", label: "Reply" },
      { to: "/recommendations", label: "Recommendations", list: true },
      { to: "/explore", label: "Explore", list: true },
      "separator",
      { to: "/trends", label: "Trends" },
      { to: "/trending-memes", label: "Trending memes" },
    ],
  },
  {
    label: "Library",
    entries: [
      { to: "/upload", label: "Upload" },
      { to: "/concepts", label: "Concepts" },
      { to: "/statistics", label: "Statistics" },
    ],
  },
  {
    label: "Cleanup",
    entries: [
      { to: "/untagged", label: "Untagged", list: true },
      { to: "/no-ocr", label: "No OCR", list: true },
      { to: "/flagged", label: "Flagged", list: true },
      { to: "/duplicates", label: "Duplicates", list: true },
      { to: "/ingestion", label: "Ingestion" },
    ],
  },
  {
    label: "Admin",
    entries: [
      { to: "/admin", label: "Batches", end: true },
      { to: "/admin/profiles", label: "Profiles" },
      { to: "/admin/recut", label: "Recut" },
      { to: "/admin/comics", label: "Comics" },
      { to: "/admin/discovery", label: "Discovery" },
    ],
  },
]

function isLink(entry: MenuEntry): entry is MenuLink {
  return entry !== "separator"
}

export default function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  // Bumped on a re-click of the already-active nav link; combined with pathname into the
  // Outlet's key below to force that one page to remount (never included in the key on its own --
  // see the key expression -- so it can't cause a remount on any other page's normal navigation).
  const [resetNonce, setResetNonce] = useState(0)
  // The open menu is remembered together with the page it was opened on, so any navigation
  // (a link, the back button) closes it without an effect.
  const [opened, setOpened] = useState<{ group: string; path: string } | null>(null)
  const openGroup = opened !== null && opened.path === location.pathname ? opened.group : null
  const navRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (openGroup === null) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpened(null)
    }
    function onMouseDown(e: MouseEvent) {
      if (navRef.current !== null && !navRef.current.contains(e.target as Node)) setOpened(null)
    }
    document.addEventListener("keydown", onKeyDown)
    document.addEventListener("mousedown", onMouseDown)
    return () => {
      document.removeEventListener("keydown", onKeyDown)
      document.removeEventListener("mousedown", onMouseDown)
    }
  }, [openGroup])

  function handleListNavClick(path: string) {
    return (e: React.MouseEvent<HTMLAnchorElement>) => {
      setOpened(null)
      if (location.pathname !== path) return
      e.preventDefault()
      setResetNonce(n => n + 1)
      navigate(path, { replace: true })
    }
  }

  function isCurrent(link: MenuLink) {
    return matchPath({ path: link.to, end: link.end ?? false }, location.pathname) !== null
  }

  return (
    <div className="min-h-screen bg-gray-100 text-gray-900">
      <header className="border-b bg-white sticky top-0 z-50">
        <nav ref={navRef} aria-label="Main" className="mx-auto max-w-6xl px-4 py-3 flex flex-wrap gap-x-6 gap-y-2">
          {MENU.map((group) => {
            const open = openGroup === group.label
            const active = group.entries.some((entry) => isLink(entry) && isCurrent(entry))
            return (
              <div key={group.label} className="relative">
                <button
                  type="button"
                  aria-haspopup="menu"
                  aria-expanded={open}
                  data-active={active ? "true" : undefined}
                  onClick={() => setOpened(open ? null : { group: group.label, path: location.pathname })}
                  className={`flex items-center gap-1 ${active ? "font-semibold text-blue-600" : "text-gray-600"}`}
                >
                  {group.label}
                  <span aria-hidden="true" className="text-xs">▾</span>
                </button>
                {open && (
                  <ul
                    role="menu"
                    aria-label={group.label}
                    className="absolute left-0 top-full mt-2 min-w-44 rounded-md border bg-white py-1 shadow-lg"
                  >
                    {group.entries.map((entry, i) =>
                      isLink(entry) ? (
                        <li key={entry.to} role="none">
                          <NavLink
                            to={entry.to}
                            end={entry.end}
                            role="menuitem"
                            onClick={entry.list ? handleListNavClick(entry.to) : () => setOpened(null)}
                            className={({ isActive }) =>
                              `block px-4 py-2 hover:bg-gray-100 ${isActive ? "font-semibold text-blue-600" : "text-gray-700"}`
                            }
                          >
                            {entry.label}
                          </NavLink>
                        </li>
                      ) : (
                        <li key={`sep-${i}`} role="separator" className="my-1 border-t" />
                      ),
                    )}
                  </ul>
                )}
              </div>
            )
          })}
        </nav>
      </header>

      {/* The ingestion review page is a side-by-side image comparison tool -- it breaks out of
          the app's reading-width cap to use the whole viewport. */}
      <main className={location.pathname === "/ingestion" ? "px-4 py-6" : "mx-auto max-w-6xl px-4 py-6"}>
        {/* location.search is deliberately excluded from this key -- the duplicates page updates
            its ?cursor= URL param on every debounced scroll tick, and including search here would
            remount the page (destroying the very scroll state we're tracking) on every one of
            those ticks instead of only on an explicit re-click. */}
        <Outlet key={`${location.pathname}:${resetNonce}`} />
      </main>
    </div>
  )
}
