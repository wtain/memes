export type FacetBucket = {
  value: string
  count: number
}

export type Facet = {
  name: string          // e.g. "tags", "language"
  buckets: FacetBucket[]
}
