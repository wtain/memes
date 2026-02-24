import { Meme } from "../types/meme"
import { HttpMemesApi } from "./http/HttpMemesApi"
import { MemesApi } from "./MemesApi"
import { MockMemesApi } from "./mock/MockMemesApi"

const MOCK_MEMES: Meme[] = [
  {
    id: "1",
    imageUrl: "https://placehold.co/400x400",
    text: ["When prod breaks"],
    tags: [
        {
            name: "dev",
            category: "env",
            score: 0.6
        },
        {
            name: "panic",
            category: "emotion",
            score: 0.7
        },
    ],
  },
  {
    id: "2",
    imageUrl: "https://placehold.co/400x400",
    text: ["It works on my machine"],
    tags: [
        {
            name: "classic",
            category: "topic",
            score: 0.6
        },
        {
            name: "dev",
            category: "env",
            score: 0.5
        },
    ],
  },
]


// const USE_MOCK = import.meta.env.DEV

const USE_MOCK = false

export async function fetchMemes(): Promise<Meme[]> {

  
  const memesApi: MemesApi = USE_MOCK ? new MockMemesApi() : new HttpMemesApi("http://127.0.0.1:8081") // import.meta.env.VITE_API_BASE_URL

  return memesApi.searchMemes({}).then(r => r.items!);

  // await new Promise((r) => setTimeout(r, 300))
  // return MOCK_MEMES
}
