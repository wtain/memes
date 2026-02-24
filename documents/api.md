Search and delivery API

```
export type MemeTag = {
  name: string        // e.g. "dev", "panic"
  category?: string  // e.g. "topic", "emotion"
  score?: number     // model confidence later
}

export type Meme = {
  id: string
  imageUrl: string
  text: string[]     // OCR lines
  tags: MemeTag[]
}

export async function fetchMemes(): Promise<Meme[]> {

export type FacetBucket = {
  value: string
  count: number
}

export type Facet = {
  name: string          // e.g. "tags", "language"
  buckets: FacetBucket[]
}



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
```


/api
    /memes
          /list
          /search
    /content
            /{id}
                 /{resolution}
    /facets
           /list