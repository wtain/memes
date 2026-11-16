import { useContext, useEffect, useState, type ReactNode } from "react"
import { Navigate, useLocation } from "react-router-dom"
import type { CapabilitiesFeatures } from "../types/generated/all"
import { fetchCapabilities } from "./capabilities"
import { FeaturesContext } from "./useFeature"
import { buildStage, DEFAULT_ROUTE, featureForPath, isFeatureVisible, type Stage } from "./visibility"

type Props = {
  children: ReactNode
  /** Defaults to the build stage. */
  stage?: Stage
  /** Defaults to the backend call; tests inject one. Only used on a prod build. */
  loadCapabilities?: (signal: AbortSignal) => Promise<CapabilitiesFeatures | null>
}

const defaultLoad = (signal: AbortSignal) =>
  fetchCapabilities(import.meta.env.VITE_BACKEND_API_URL, signal)

/**
 * Reads the capabilities once at startup (prod builds only: a dev build shows everything, so the
 * call would change nothing). Until it answers, or when it fails, the build flag alone decides.
 */
export function FeaturesProvider({ children, stage = buildStage(), loadCapabilities = defaultLoad }: Props) {
  const [capabilities, setCapabilities] = useState<CapabilitiesFeatures | null>(null)

  useEffect(() => {
    if (stage !== "prod") return
    const controller = new AbortController()
    loadCapabilities(controller.signal).then(
      result => { if (!controller.signal.aborted) setCapabilities(result) },
      () => undefined,
    )
    return () => controller.abort()
  }, [stage, loadCapabilities])

  return <FeaturesContext.Provider value={{ stage, capabilities }}>{children}</FeaturesContext.Provider>
}

/** Layout-level guard: a hidden route goes to the default browse route, whatever the path under it. */
export function FeatureRouteGuard({ children }: { children: ReactNode }) {
  const { stage, capabilities } = useContext(FeaturesContext)
  const { pathname } = useLocation()
  const feature = featureForPath(pathname)
  if (feature !== null && !isFeatureVisible(feature, stage, capabilities)) {
    return <Navigate to={DEFAULT_ROUTE} replace />
  }
  return <>{children}</>
}
