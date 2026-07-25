import { Outlet, NavLink } from "react-router-dom"

export default function AppLayout() {
  return (
    <div className="min-h-screen bg-gray-100 text-gray-900">
      <header className="border-b bg-white sticky top-0 z-50">
        <div className="mx-auto max-w-6xl px-4 py-3 flex gap-6">
          <NavLink
            to="/explore"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Explore
          </NavLink>

          <NavLink
            to="/untagged"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Untagged
          </NavLink>

          <NavLink
            to="/duplicates"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Duplicates
          </NavLink>

          <NavLink
            to="/no-ocr"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            No OCR
          </NavLink>

          <NavLink
            to="/flagged"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Flagged
          </NavLink>

          <NavLink
            to="/search"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Search
          </NavLink>

          <NavLink
            to="/recommendations"
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
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
