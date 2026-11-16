import type { CapabilitiesFeatures } from "../types/generated/all"

/** Build-time stage. "prod" is a public, read-only deployment; everything else is the dev workstation. */
export type Stage = "dev" | "prod"

/** Everything the app can hide on prod. Browsing (search, explore, meme page, trending memes) is never listed. */
export type Feature =
  | "cleanup"    // Cleanup menu group and its routes (untagged, no OCR, flagged, duplicates, ingestion)
  | "admin"      // Admin menu group and its routes (batches, profiles, recut, comics editor, discovery)
  | "statistics"
  | "trends"     // the news Trends pages (not the trending memes list)
  | "reply"      // /reply (meme from text)
  | "upload"
  | "history"    // history screens
  | "curator"    // MemePage curator controls: profile editor, description feedback, note, flag toggle, popularity set control
  | "comics"     // MemeComicsBlock, the card "Add to comics" action, every /api/comics call
  | "concepts"

/** What a prod build still shows; every other feature is hidden there. */
const SHOWN_ON_PROD: ReadonlySet<Feature> = new Set<Feature>(["concepts"])

/** The backend capability flag that must also be on for a feature, or null when the feature has none. */
const CAPABILITY: Record<Feature, keyof CapabilitiesFeatures | null> = {
  cleanup: "admin",
  admin: "admin",
  statistics: "statistics",
  trends: "trends",
  reply: "memeFromText",
  upload: "uploads",
  history: "history",
  curator: null,
  comics: "comics",
  concepts: "concepts",
}

export const ALL_FEATURES = Object.keys(CAPABILITY) as Feature[]

/** The stage baked into this build (`VITE_STAGE=prod`); anything else is dev. */
export function buildStage(): Stage {
  return import.meta.env.VITE_STAGE === "prod" ? "prod" : "dev"
}

/**
 * Effective visibility = build flag AND capability. A dev build shows everything and ignores
 * capabilities. A prod build shows only SHOWN_ON_PROD, and a capability that is explicitly
 * false hides one of those too; a missing capabilities answer (null: not loaded, failed or 404)
 * leaves the build flag alone. A capability can only reduce.
 */
export function isFeatureVisible(
  feature: Feature,
  stage: Stage,
  capabilities: CapabilitiesFeatures | null,
): boolean {
  if (stage === "dev") return true
  if (!SHOWN_ON_PROD.has(feature)) return false
  const key = CAPABILITY[feature]
  if (key === null || capabilities === null) return true
  return capabilities[key] !== false
}

/** Path prefixes owned by a feature, for redirects. */
const ROUTE_FEATURES: ReadonlyArray<readonly [string, Feature]> = [
  ["/admin", "admin"],
  ["/untagged", "cleanup"],
  ["/no-ocr", "cleanup"],
  ["/flagged", "cleanup"],
  ["/duplicates", "cleanup"],
  ["/ingestion", "cleanup"],
  ["/statistics", "statistics"],
  ["/trends", "trends"],
  ["/reply", "reply"],
  ["/upload", "upload"],
  ["/history", "history"],
  ["/concepts", "concepts"],
]

/** The feature that owns `pathname`, or null for a path that is always available. */
export function featureForPath(pathname: string): Feature | null {
  for (const [prefix, feature] of ROUTE_FEATURES) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return feature
  }
  return null
}

/** Where a hidden route goes. */
export const DEFAULT_ROUTE = "/explore"
