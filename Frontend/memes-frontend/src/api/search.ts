import { Meme } from "../types/meme"
import { Facet } from "../types/facet"

export type SearchResponse = {
  results: Meme[]
  facets: Facet[]
  nextCursor?: string
}

export async function searchMemes(params: {
  query?: string
  filters?: Record<string, string[]>
  cursor?: string
}): Promise<SearchResponse> {
  await new Promise((r) => setTimeout(r, 300))

  return {
    results: [
        {
            id: "1",
            imageUrl: "https://placehold.co/400x400",
            text: ["When prod breaks", "on Friday"],
            tags: [
            { name: "dev", category: "topic", score: 0.98 },
            { name: "panic", category: "emotion", score: 0.91 },
            ],
        },
        {
            id: "2",
            imageUrl: "https://placehold.co/400x400",
            text: ["It works on my machine"],
            tags: [
            { name: "dev", category: "topic" },
            ],
        },
        ],

    facets: [
      {
        name: "tags",
        buckets: [
          { value: "dev", count: 124 },
          { value: "panic", count: 52 },
          { value: "politics", count: 98 },
        ],
      },
      {
        name: "language",
        buckets: [
          { value: "en", count: 302 },
          { value: "ru", count: 47 },
        ],
      },
    ],
    nextCursor: params.cursor ? undefined : "cursor_page_2",
  }
}
