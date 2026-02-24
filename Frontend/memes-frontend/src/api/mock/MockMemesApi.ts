import { MemesApi } from "../MemesApi"
import { Meme, MemeSearchRequest, MemeSearchResponse } from "../../types/generated/all"

const ALL_MEMES: Meme[] = Array.from({ length: 50 }).map((_, i) => ({
  id: `meme_${i + 1}`,
  text: [`Meme #${i + 1}`],
  imageUrl: `/mock-images/meme_${i + 1}.jpg`,
  tags: i % 2 === 0 ? [
        {
            name: "classic"
        }
    ] : [
        { name: "modern" }
    ],
}))

export class MockMemesApi implements MemesApi {
  async searchMemes(
    request: MemeSearchRequest
  ): Promise<MemeSearchResponse> {
    const {
      query,
      cursor,
      limit = 20,
      tags,
    } = request

    let filtered = ALL_MEMES

    if (query) {
      filtered = filtered.filter(m =>
        m.text?.some(t => t.toLowerCase().includes(query.toLowerCase()))
      )
    }

    if (tags && tags.length > 0) {
      filtered = filtered.filter(m =>
        tags.every(f => m.tags?.includes(f))
      )
    }

    const startIndex = cursor
      ? filtered.findIndex(m => m.id === cursor) + 1
      : 0

    const items = filtered.slice(startIndex, startIndex + limit)
    const nextCursor =
      startIndex + limit < filtered.length
        ? items[items.length - 1]?.id ?? null
        : undefined

    return Promise.resolve({
      items,
      nextCursor,
    })
  }
}
