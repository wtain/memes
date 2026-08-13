import { useState } from "react"
import { Outlet, NavLink, useLocation, useNavigate } from "react-router-dom"

// handleListNavClick below is wired up on the NavLinks for /explore, /untagged, /duplicates,
// /no-ocr, /flagged, /search, /recommendations -- the pages backed by MemesList/
// MemesDuplicatesList's windowed infinite scroll. React Router doesn't navigate (or remount
// anything) when you click a NavLink to the route you're already on, so without this, scroll
// position/pagination state on these pages was otherwise unreachable except via a full page
// refresh.

export default function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  // Bumped on a re-click of the already-active nav link; combined with pathname into the
  // Outlet's key below to force that one page to remount (never included in the key on its own --
  // see the key expression -- so it can't cause a remount on any other page's normal navigation).
  const [resetNonce, setResetNonce] = useState(0)

  function handleListNavClick(path: string) {
    return (e: React.MouseEvent<HTMLAnchorElement>) => {
      if (location.pathname !== path) return
      e.preventDefault()
      setResetNonce(n => n + 1)
      navigate(path, { replace: true })
    }
  }

  return (
    <div className="min-h-screen bg-gray-100 text-gray-900">
      <header className="border-b bg-white sticky top-0 z-50">
        <div className="mx-auto max-w-6xl px-4 py-3 flex gap-6">
          <NavLink
            to="/explore"
            onClick={handleListNavClick("/explore")}
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Explore
          </NavLink>

          <NavLink
            to="/untagged"
            onClick={handleListNavClick("/untagged")}
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Untagged
          </NavLink>

          <NavLink
            to="/duplicates"
            onClick={handleListNavClick("/duplicates")}
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Duplicates
          </NavLink>

          <NavLink
            to="/no-ocr"
            onClick={handleListNavClick("/no-ocr")}
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            No OCR
          </NavLink>

          <NavLink
            to="/flagged"
            onClick={handleListNavClick("/flagged")}
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Flagged
          </NavLink>

          <NavLink
            to="/search"
            onClick={handleListNavClick("/search")}
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Search
          </NavLink>

          <NavLink
            to="/recommendations"
            onClick={handleListNavClick("/recommendations")}
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Recommendations
          </NavLink>

          <NavLink
            to="/trends"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Trends
          </NavLink>

          <NavLink
            to="/concepts"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Concepts
          </NavLink>

          <NavLink
            to="/upload"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Upload
          </NavLink>

          <NavLink
            to="/statistics"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Statistics
          </NavLink>

          <NavLink
            to="/ingestion"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Ingestion
          </NavLink>

          <NavLink
            to="/admin"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Admin
          </NavLink>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">
        {/* location.search is deliberately excluded from this key -- the duplicates page updates
            its ?cursor= URL param on every debounced scroll tick, and including search here would
            remount the page (destroying the very scroll state we're tracking) on every one of
            those ticks instead of only on an explicit re-click. */}
        <Outlet key={`${location.pathname}:${resetNonce}`} />
      </main>
    </div>
  )
}
