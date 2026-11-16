import type { CapabilitiesFeatures, CapabilitiesResponse } from "../types/generated/all"

/**
 * Reads GET /api/diagnostics/capabilities. Never throws: a network error, a non-2xx answer
 * (404 on an older backend) or an unusable body answers null, which means "rely on the build flag only".
 */
export async function fetchCapabilities(baseUrl: string, signal?: AbortSignal): Promise<CapabilitiesFeatures | null> {
  try {
    const response = await fetch(`${baseUrl}/api/diagnostics/capabilities`, {
      headers: { Accept: "application/json" },
      signal,
    })
    if (!response.ok) return null
    const body = (await response.json()) as Partial<CapabilitiesResponse> | null
    const features = body?.features
    if (features === null || features === undefined || typeof features !== "object") return null
    return features
  } catch {
    return null
  }
}
