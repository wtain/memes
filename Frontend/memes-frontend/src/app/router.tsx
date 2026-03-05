import { createBrowserRouter, Navigate } from "react-router-dom"
import AppLayout from "./AppLayout"
import ExplorePage from "../pages/ExplorePage"
import SearchPage from "../pages/SearchPage"
import { MemesApi } from "../api/MemesApi";
import { MockMemesApi } from "../api/mock/MockMemesApi";
import { HttpMemesApi } from "../api/http/HttpMemesApi";

const baseUrl = "http://127.0.0.1:8081";

// const USE_MOCK = import.meta.env.DEV
const USE_MOCK = false;

const memesApi: MemesApi = USE_MOCK ? new MockMemesApi() : new HttpMemesApi(baseUrl) // import.meta.env.VITE_API_BASE_URL

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <Navigate to="/explore" replace /> },
      { path: "/explore", element: <ExplorePage memesApi={memesApi} baseUrl={baseUrl} /> },
      { path: "/search", element: <SearchPage memesApi={memesApi} baseUrl={baseUrl} /> },
    ],
  },
])
