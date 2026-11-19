import { Card } from "./Card"

export function Skeleton({ className, ...rest }: React.ComponentProps<"div">) {
  const classes = ["rounded-md bg-line motion-safe:animate-pulse", className].filter(Boolean).join(" ")
  return <div {...rest} aria-hidden="true" className={classes} />
}

export function MemeCardSkeleton() {
  return (
    <Card aria-hidden="true" className="flex h-full flex-col">
      <Skeleton className="aspect-square w-full rounded-none" />
      <div className="flex h-10 items-center gap-2 px-2">
        <Skeleton className="h-4 w-16" />
        <Skeleton className="h-4 w-12" />
      </div>
    </Card>
  )
}
