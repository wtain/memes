export type Variant = "primary" | "secondary" | "ghost" | "danger"
export type Size = "sm" | "md" | "lg"

export const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand text-on-brand hover:bg-brand-hover",
  secondary: "bg-surface text-fg ring-1 ring-inset ring-line-strong hover:bg-surface-sunken",
  ghost: "text-fg-muted hover:bg-black/5",
  danger: "bg-danger text-on-danger hover:bg-danger-hover",
}

export const SIZES: Record<Size, string> = {
  sm: "min-h-8 px-2 text-sm pointer-coarse:min-h-11",
  md: "min-h-10 px-4 text-sm",
  lg: "min-h-11 px-6 text-base",
}

export const ICON_SIZES: Record<Size, string> = {
  sm: "min-h-8 min-w-8 pointer-coarse:min-h-11 pointer-coarse:min-w-11",
  md: "min-h-10 min-w-10",
  lg: "min-h-11 min-w-11",
}

export const BASE =
  "inline-flex items-center justify-center gap-2 rounded-md font-medium cursor-pointer motion-safe:transition-colors disabled:opacity-50 disabled:pointer-events-none"

/** The class string of a text button, for elements that are not a <button> (router links). */
export function buttonClasses({
  variant = "secondary",
  size = "md",
  className,
}: { variant?: Variant; size?: Size; className?: string } = {}): string {
  return [BASE, VARIANTS[variant], SIZES[size], className].filter(Boolean).join(" ")
}
