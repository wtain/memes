import { createBrowserRouter, Navigate } from "react-router-dom"
import AppLayout from "./AppLayout"
import ExplorePage from "../pages/ExplorePage"
import SearchPage from "../pages/SearchPage"
import type { MemesApi } from "../api/MemesApi";
import { HttpMemesApi } from "../api/http/HttpMemesApi";
import ConceptsPage from "../pages/ConceptsPage";
import MemePage from "../pages/MemePage";
import ConceptPage from "../pages/ConceptPage";
import ExploreUntaggedPage from "../pages/ExploreUntaggedPage";
import ExploreDuplicatesPage from "../pages/ExploreDuplicatesPage";
import ExploreNoOcrPage from "../pages/ExploreNoOcrPage";
import ExploreFlaggedPage from "../pages/ExploreFlaggedPage";
import TrendsPage from "../pages/TrendsPage";
import TrendsDatePage from "../pages/TrendsDatePage";
import TrendHistoryPage from "../pages/TrendHistoryPage";
import UploadPage from "../pages/UploadPage";
import RecommendationsPage from "../pages/RecommendationsPage";
import StatisticsPage from "../pages/StatisticsPage";


// const baseUrl = "http://127.0.0.1:8081";
const baseUrl = import.meta.env.VITE_BACKEND_API_URL;

const memesApi: MemesApi = new HttpMemesApi(baseUrl)

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <Navigate to="/explore" replace /> },
      { path: "/explore", element: <ExplorePage memesApi={memesApi} /> },
      { path: "/untagged", element: <ExploreUntaggedPage memesApi={memesApi} /> },
      { path: "/duplicates", element: <ExploreDuplicatesPage memesApi={memesApi} /> },
      { path: "/no-ocr", element: <ExploreNoOcrPage memesApi={memesApi} /> },
      { path: "/flagged", element: <ExploreFlaggedPage memesApi={memesApi} /> },
      { path: "/trends", element: <TrendsPage memesApi={memesApi} /> },
      { path: "/trends/date/:date", element: <TrendsDatePage memesApi={memesApi} /> },
      { path: "/trends/history/:label/:name", element: <TrendHistoryPage memesApi={memesApi} /> },
      { path: "/search", element: <SearchPage memesApi={memesApi} /> },
      { path: "/recommendations", element: <RecommendationsPage memesApi={memesApi} /> },
      { path: "/concepts", element: <ConceptsPage memesApi={memesApi} /> },
      { path: "/upload", element: <UploadPage memesApi={memesApi} /> },
      { path: "/statistics", element: <StatisticsPage memesApi={memesApi} /> },
      {
        path: "/memes/:id",
        element: <MemePage memesApi={memesApi} />
      },
      {
        path: "/concepts/:id",
        element: <ConceptPage memesApi={memesApi} />
      },
    ],
  },
])
