import { Meme } from "../types/meme"

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

