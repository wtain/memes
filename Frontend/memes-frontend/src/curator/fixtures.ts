import type { CuratedProfile, ProfileCoverage, ProfileCoverageImages } from "./types"

export function makeCuratedProfile(overrides: Partial<CuratedProfile> = {}): CuratedProfile {
  const defaults: CuratedProfile = {
    imageId: "img-1",
    exists: true,
    humanVerified: false,
    needsReview: false,
    taxonomyVersion: 2,
    currentTaxonomyVersion: 2,
    situation: "Manager scheduled a late meeting.",
    situationConfidence: null,
    useWhen: null,
    values: {
      emotion: [{ key: "annoyance", confidence: 0.9, source: "model" }],
      stance: [{ key: "sarcasm", confidence: 0.6, source: "model" }],
      topic: [{ key: "work", confidence: 0.9, source: "model" }],
      tone: [],
    },
    vector: "fresh",
    extractedAt: "2026-10-09T10:00:00",
    updatedAt: "2026-10-09T10:00:00",
  }
  return { ...defaults, ...overrides }
}

export function makeCoverage(overrides: Partial<ProfileCoverage> = {}): ProfileCoverage {
  const defaults: ProfileCoverage = {
    active: 100,
    profiled: 75,
    withoutProfile: 25,
    humanVerified: 10,
    needsReview: 5,
    staleTaxonomy: 3,
    failed: 2,
    pendingVector: 8,
    currentTaxonomyVersion: 2,
    lastRuns: {
      profiles: {
        runId: "run-1",
        status: "done",
        startedAt: "2026-10-09T09:00:00",
        completedAt: "2026-10-09T10:00:00",
        stats: { images_selected: 100, images_remaining: 0 },
      },
      embeddings: {
        runId: "run-2",
        status: "done",
        startedAt: "2026-10-09T10:00:00",
        completedAt: "2026-10-09T10:30:00",
        stats: { images_processed: 50 },
      },
    },
  }
  return { ...defaults, ...overrides }
}

export function makeCoverageImages(
  ids: string[] = ["img-1"],
  nextCursor: string | null = null
): ProfileCoverageImages {
  return {
    items: ids.map((id) => ({
      id,
      imageUrl: `/api/images/${id}`,
      humanVerified: false,
    })),
    nextCursor,
    total: ids.length,
  }
}
