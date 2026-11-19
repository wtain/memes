type Tone = "neutral" | "success" | "warning" | "danger" | "brand"

const TONES: Record<Tone, string> = {
  neutral: "bg-line text-fg-muted",
  success: "bg-success-bg text-success-fg",
  warning: "bg-warning-bg text-warning-fg",
  danger: "bg-danger-bg text-danger-fg",
  brand: "bg-brand/10 text-brand",
}

export function Badge({ tone = "neutral", className, ...rest }: React.ComponentProps<"span"> & { tone?: Tone }) {
  const classes = ["inline-flex items-center rounded-full px-2 py-0.5 text-meta font-medium", TONES[tone], className]
    .filter(Boolean)
    .join(" ")
  return <span {...rest} className={classes} />
}
