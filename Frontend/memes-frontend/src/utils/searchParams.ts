
export function parseSearchParams(params: URLSearchParams) {
  const filters: Record<string, string[]> = {}

  params.forEach((value, key) => {
    if (key === "q" || key === "cursor") return
    filters[key] = [...(filters[key] ?? []), value]
  })

  return {
    query: params.get("q") ?? "",
    cursor: params.get("cursor") ?? undefined,
    filters,
  }
}

export function buildSearchParams(
  query: string,
  filters: Record<string, string[]>,
  cursor?: string
) {
  const params = new URLSearchParams()

  if (query) params.set("q", query)
  if (cursor) params.set("cursor", cursor)

  Object.entries(filters).forEach(([facet, values]) => {
    values.forEach((v) => params.append(facet, v))
  })

  return params
}
