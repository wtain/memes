export function Card({
  padded = false,
  interactive = false,
  className,
  ...rest
}: React.ComponentProps<"div"> & { padded?: boolean; interactive?: boolean }) {
  const classes = [
    "rounded-lg bg-surface shadow-card ring-1 ring-line overflow-hidden",
    padded ? "p-4" : "",
    interactive ? "motion-safe:transition-shadow hover:shadow-card-hover" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ")
  return <div {...rest} className={classes} />
}
