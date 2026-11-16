import { createContext, useContext } from "react"
import type { CapabilitiesFeatures } from "../types/generated/all"
import { buildStage, isFeatureVisible, type Feature, type Stage } from "./visibility"

export type FeaturesValue = { stage: Stage; capabilities: CapabilitiesFeatures | null }

// Without a provider (unit tests of single components) the build stage applies: nothing is hidden on dev.
export const FeaturesContext = createContext<FeaturesValue>({ stage: buildStage(), capabilities: null })

export function useFeature(feature: Feature): boolean {
  const { stage, capabilities } = useContext(FeaturesContext)
  return isFeatureVisible(feature, stage, capabilities)
}

/** The same check for a list of features (a menu), where a hook per entry is not possible. */
export function useFeatureCheck(): (feature: Feature) => boolean {
  const { stage, capabilities } = useContext(FeaturesContext)
  return feature => isFeatureVisible(feature, stage, capabilities)
}
