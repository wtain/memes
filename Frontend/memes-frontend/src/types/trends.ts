export type TrendEntry = {
  label: string
  name: string
  value: number
}

export type TrendHistoryEntry = {
  runId: string
  date: string
  label: string
  name: string
  value: number
}

export type TrendsRunDto = {
  runId: string
  createdAt: string
  status: string
}
