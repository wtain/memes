import { Outlet, NavLink } from "react-router-dom"

export default function AppLayout() {
  return (
    <div className="min-h-screen bg-gray-100 text-gray-900">
      <header className="border-b bg-white">
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
            to="/search"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Search
          </NavLink>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
