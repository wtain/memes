import type { CuratedProfile, CuratedProfileSaveRequest, ProfileCoverage, ProfileCoverageImages } from "../types/generated/all"

// Re-exports from generated types
export type { CuratedProfile, CuratedProfileSaveRequest, ProfileCoverage, ProfileCoverageImages }

// Profile coverage image item type
export type ProfileCoverageImage = ProfileCoverageImages["items"][number]

// Category type and choices
export type Category = "without_profile" | "needs_review" | "stale_taxonomy" | "failed" | "human_verified" | "pending_vector"

export const CATEGORY_CHOICES = [
  { key: "without_profile" as const, label: "Without profile", count: (coverage: ProfileCoverage) => coverage.withoutProfile },
  { key: "needs_review" as const, label: "Needs review", count: (coverage: ProfileCoverage) => coverage.needsReview },
  { key: "stale_taxonomy" as const, label: "Stale vocabulary", count: (coverage: ProfileCoverage) => coverage.staleTaxonomy },
  { key: "failed" as const, label: "Failed", count: (coverage: ProfileCoverage) => coverage.failed },
  { key: "human_verified" as const, label: "Human-verified", count: (coverage: ProfileCoverage) => coverage.humanVerified },
  { key: "pending_vector" as const, label: "Search vector pending", count: (coverage: ProfileCoverage) => coverage.pendingVector },
] as const
