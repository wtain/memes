export interface StatisticsMemeStats {
  total: number
  with_embeddings: number
  with_ocr: number
  with_tags: number
  with_descriptions: number
  with_concept_tags: number
  excluded: number
}

export interface StatisticsContentStats {
  ocr_texts: number
  tags: number
  tag_keys: number
  tag_values: number
  concepts: number
  concept_image_sets: number
  concept_images: number
}

export interface StatisticsTrendsStats {
  runs: number
  feed_sources: number
}

export interface StatisticsResponse {
  memes: StatisticsMemeStats
  content: StatisticsContentStats
  trends: StatisticsTrendsStats
}