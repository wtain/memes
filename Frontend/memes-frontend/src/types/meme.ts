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
