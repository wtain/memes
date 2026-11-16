import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router-dom"
import { router } from "./app/router"
import { FeaturesProvider } from "./features/FeaturesProvider"
import "./index.css"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <FeaturesProvider>
      <RouterProvider router={router} />
    </FeaturesProvider>
  </StrictMode>
)
