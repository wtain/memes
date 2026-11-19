type Props = {
  title: string
  description?: string
  action?: React.ReactNode
  icon?: React.ReactNode
  tone?: "empty" | "error"
}

export function EmptyState({ title, description, action, icon, tone = "empty" }: Props) {
  return (
    <div role={tone === "error" ? "alert" : "status"} className="flex flex-col items-center gap-2 py-12 text-center">
      {icon && <div aria-hidden="true" className="text-fg-subtle">{icon}</div>}
      <p className="text-base font-medium text-fg">{title}</p>
      {description && <p className="text-sm text-fg-muted">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
