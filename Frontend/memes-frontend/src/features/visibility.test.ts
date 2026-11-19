import type { CapabilitiesFeatures } from "../types/generated/all"
import { ALL_FEATURES, buildStage, DEFAULT_ROUTE, featureForPath, isFeatureVisible } from "./visibility"

const ALL_ON: CapabilitiesFeatures = {
  memeFromText: true, concepts: true, trends: true, comics: true, ratings: true,
  statistics: true, uploads: true, history: true, admin: true,
}
const ALL_OFF: CapabilitiesFeatures = {
  memeFromText: false, concepts: false, trends: false, comics: false, ratings: false,
  statistics: false, uploads: false, history: false, admin: false,
}

describe("isFeatureVisible", () => {
  it("shows everything on a dev build, whatever the capabilities say or fail to say", () => {
    for (const feature of ALL_FEATURES) {
      expect(isFeatureVisible(feature, "dev", null)).toBe(true)
      expect(isFeatureVisible(feature, "dev", ALL_OFF)).toBe(true)
      expect(isFeatureVisible(feature, "dev", ALL_ON)).toBe(true)
    }
  })

  it("hides the whole write/admin surface on a prod build, even when the capabilities are all on", () => {
    const hidden = ALL_FEATURES.filter(f => f !== "concepts")
    expect(hidden).toEqual(expect.arrayContaining([
      "cleanup", "admin", "statistics", "trends", "reply", "upload", "history", "curator", "comics",
    ]))
    for (const feature of hidden) {
      expect(isFeatureVisible(feature, "prod", ALL_ON)).toBe(false)
      expect(isFeatureVisible(feature, "prod", null)).toBe(false)
    }
  })

  it("keeps what the prod build shows when the capabilities are missing", () => {
    expect(isFeatureVisible("concepts", "prod", null)).toBe(true)
    expect(isFeatureVisible("concepts", "prod", ALL_ON)).toBe(true)
  })

  it("lets a capability reduce a feature the prod build shows, never add one", () => {
    expect(isFeatureVisible("concepts", "prod", { ...ALL_ON, concepts: false })).toBe(false)
    expect(isFeatureVisible("comics", "prod", ALL_ON)).toBe(false)
  })
})

describe("buildStage", () => {
  it("is dev when VITE_STAGE is unset", () => {
    expect(buildStage()).toBe("dev")
  })

  it("is prod only for the exact value prod", () => {
    vi.stubEnv("VITE_STAGE", "prod")
    expect(buildStage()).toBe("prod")
    vi.stubEnv("VITE_STAGE", "production")
    expect(buildStage()).toBe("dev")
    vi.unstubAllEnvs()
  })
})

describe("featureForPath", () => {
  it.each([
    ["/admin", "admin"], ["/admin/comics/abc", "admin"], ["/admin/discovery", "admin"],
    ["/untagged", "cleanup"], ["/no-ocr", "cleanup"], ["/flagged", "cleanup"], ["/duplicates", "cleanup"], ["/ingestion", "cleanup"],
    ["/statistics", "statistics"], ["/trends", "trends"], ["/trends/date/2026-01-01", "trends"],
    ["/trends/history/PERSON/x", "trends"], ["/reply", "reply"], ["/upload", "upload"],
    ["/concepts", "concepts"], ["/concepts/3", "concepts"],
  ])("%s belongs to %s", (path, feature) => {
    expect(featureForPath(path)).toBe(feature)
  })

  it.each([
    ["/Admin", "admin"], ["/STATISTICS", "statistics"], ["/Admin/Comics/X", "admin"], ["/admin/", "admin"],
    ["/statistics///", "statistics"], ["//admin", "admin"], ["/Trends/Date/1", "trends"], ["/NO-OCR", "cleanup"],
  ])("%s is matched case-insensitively and ignoring extra slashes (%s)", (path, feature) => {
    expect(featureForPath(path)).toBe(feature)
  })

  it.each(["/", "/Administrator", "/ADMINISTRATOR/"])("%s is not mistaken for a hidden prefix", path => {
    expect(featureForPath(path)).toBeNull()
  })

  it("sends hidden routes to Home, which is itself always available (no redirect loop)", () => {
    expect(DEFAULT_ROUTE).toBe("/")
    expect(featureForPath(DEFAULT_ROUTE)).toBeNull()
  })

  it.each(["/", "/explore", "/search", "/recommendations", "/trending-memes", "/memes/abc", "/administrator"])(
    "%s is always available", path => {
      expect(featureForPath(path)).toBeNull()
    })
})
