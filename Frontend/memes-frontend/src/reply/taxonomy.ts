import type { AxisName, TaxonomyResponse } from "./types"
import { DEFAULT_MAX_VALUES } from "./types"

type Axis = TaxonomyResponse["axes"][number]

export type PickerOption = { key: string; label: string; definition: string }

export function axisSpec(taxonomy: TaxonomyResponse | null, axis: string): Axis | undefined {
  return taxonomy?.axes.find((candidate) => candidate.name === axis)
}

export function maxValuesFor(taxonomy: TaxonomyResponse | null, axis: AxisName): number {
  return axisSpec(taxonomy, axis)?.maxValues ?? DEFAULT_MAX_VALUES[axis]
}

export function optionsFor(taxonomy: TaxonomyResponse | null, axis: AxisName): PickerOption[] {
  return (axisSpec(taxonomy, axis)?.values ?? []).map((value) => ({
    key: value.key, label: value.labels.en || value.key, definition: value.definition,
  }))
}

/** The English label of a value, or its key while the vocabulary has not loaded (or the key is unknown). */
export function labelFor(taxonomy: TaxonomyResponse | null, axis: string, key: string): string {
  const value = axisSpec(taxonomy, axis)?.values?.find((candidate) => candidate.key === key)
  return value?.labels.en || key
}
