import type { MemeFromTextResponse, MemeFromTextResult, QueryProfile, TaxonomyResponse } from "./types"

function value(key: string, en: string, ru: string, es: string) {
  return { key, definition: `${en} (definition)`, labels: { en, ru, es } }
}

export const TEST_TAXONOMY: TaxonomyResponse = {
  version: 2,
  axes: [
    { name: "situation", type: "free_text", maxChars: 160 },
    {
      name: "emotion", type: "vocabulary", maxValues: 3,
      values: [value("annoyance", "Annoyance", "Раздражение", "Fastidio"), value("joy", "Joy", "Радость", "Alegría"),
               value("resignation", "Resignation", "Смирение", "Resignación"), value("pride", "Pride", "Гордость", "Orgullo")],
    },
    {
      name: "stance", type: "vocabulary", maxValues: 2,
      values: [value("sarcasm", "Sarcasm", "Сарказм", "Sarcasmo"), value("congratulate", "Congratulate", "Поздравить", "Felicitar")],
    },
    {
      name: "topic", type: "vocabulary", maxValues: 3,
      values: [value("work", "Work", "Работа", "Trabajo"), value("food", "Food", "Еда", "Comida")],
    },
    {
      name: "tone", type: "vocabulary", maxValues: 2,
      values: [value("sarcastic", "Sarcastic", "Саркастичный", "Sarcástico"), value("wholesome", "Wholesome", "Добрый", "Sano")],
    },
  ],
}

export function makeProfile(overrides: Partial<QueryProfile> = {}): QueryProfile {
  return {
    taxonomyVersion: 2, language: "en", parserVersion: "p3-test", degraded: false,
    situation: "Manager scheduled a late meeting.", situationConfidence: null,
    emotion: [{ key: "annoyance", confidence: 0.9, source: "model" }],
    stance: [{ key: "sarcasm", confidence: 0.6, source: "model" }],
    topic: [{ key: "work", confidence: 0.9, source: "model" }],
    tone: [],
    ...overrides,
  }
}

export function makeResult(id: string, overrides: Partial<MemeFromTextResult> = {}): MemeFromTextResult {
  return {
    meme: { id, imageUrl: `/api/images/${id}`, text: [], tags: [], flagged: false },
    score: 0.8, axisPath: true,
    explanation: {
      axes: [
        { axis: "situation", score: 0.7, weight: 0.4, contribution: 0.28, neutral: false, queryValues: ["Manager scheduled a late meeting."], matched: [] },
        { axis: "emotion", score: 0.9, weight: 0.6, contribution: 0.54, neutral: false, queryValues: ["annoyance"],
          matched: [{ queryKey: "annoyance", memeKey: "resignation", similarity: 0.8, memeConfidence: 0.9 }] },
      ],
      situationText: "A worker accepts another late meeting.", useWhen: "Use when plans change at the last minute.",
    },
    ...overrides,
  }
}

export function makeResponse(overrides: Partial<MemeFromTextResponse> = {}): MemeFromTextResponse {
  return {
    items: [], facets: [], hasNext: false, profile: makeProfile(), degraded: false, fallback: "none",
    meta: { available: true, parseMs: 700, embedMs: 15, retrievalMs: 110, candidates: 12, cacheHit: false, parserVersion: "p3-test", model: "m", queryHash: "a".repeat(64) },
    ...overrides,
  }
}
