import { createBrowserRouter, Navigate } from "react-router-dom"
import AppLayout from "./AppLayout"
import ExplorePage from "../pages/ExplorePage"
import SearchPage from "../pages/SearchPage"

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <Navigate to="/explore" replace /> },
      { path: "/explore", element: <ExplorePage baseUrl="http://127.0.0.1:8081" /> },
      { path: "/search", element: <SearchPage /> },
    ],
  },
])
