import { createBrowserRouter, Navigate } from "react-router-dom"
import AppLayout from "./AppLayout"
import ExplorePage from "../pages/ExplorePage"
import SearchPage from "../pages/SearchPage"
import { MemesApi } from "../api/MemesApi";
import { HttpMemesApi } from "../api/http/HttpMemesApi";
import ConceptsPage from "../pages/ConceptsPage";

const baseUrl = "http://127.0.0.1:8081";

const memesApi: MemesApi = new HttpMemesApi(baseUrl)

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <Navigate to="/explore" replace /> },
      { path: "/explore", element: <ExplorePage memesApi={memesApi} /> },
      { path: "/search", element: <SearchPage memesApi={memesApi} /> },
      { path: "/concepts", element: <ConceptsPage memesApi={memesApi} /> },
    ],
  },
])
