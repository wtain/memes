import type { RecutDecision, RecutProposal, RecutProposalDetail, RecutProposalPage } from "./types"

export function makeDecision(id: string, state: string = "approved"): RecutDecision {
  return { id, state, decidedAt: "2026-10-29T10:00:00+00:00" }
}

export function makeProposal(id: string, overrides: Partial<RecutProposal> = {}): RecutProposal {
  return {
    id,
    sourceImageId: `src-${id}`,
    cutImageId: `cut-${id}`,
    confidence: 0.9,
    box: { x: 10, y: 20, w: 300, h: 200 },
    ...overrides,
  }
}

export function makeProposalPage(ids: string[], total: number = ids.length): RecutProposalPage {
  return { items: ids.map((id) => makeProposal(id)), total }
}

export function makeProposalDetail(id: string, overrides: Partial<RecutProposalDetail> = {}): RecutProposalDetail {
  return {
    ...makeProposal(id),
    state: "proposed",
    detector: "margin_trim",
    detectorVersion: "1",
    detectRunId: null,
    createdAt: "2026-10-28T10:00:00+00:00",
    previousId: null,
    nextId: null,
    ...overrides,
  }
}
