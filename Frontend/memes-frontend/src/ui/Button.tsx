type Variant = "primary" | "secondary" | "ghost" | "danger"
type Size = "sm" | "md" | "lg"

type BaseProps = {
  variant?: Variant
  size?: Size
  pressed?: boolean
  loading?: boolean
}
type TextButtonProps = React.ComponentProps<"button"> & BaseProps & { iconOnly?: false }
type IconButtonProps = React.ComponentProps<"button"> & BaseProps & { iconOnly: true; "aria-label": string }

const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand text-on-brand hover:bg-brand-hover",
  secondary: "bg-surface text-fg ring-1 ring-inset ring-line-strong hover:bg-surface-sunken",
  ghost: "text-fg-muted hover:bg-black/5",
  danger: "bg-danger text-on-danger hover:bg-danger-hover",
}

const SIZES: Record<Size, string> = {
  sm: "min-h-8 px-2 text-sm pointer-coarse:min-h-11",
  md: "min-h-10 px-4 text-sm",
  lg: "min-h-11 px-6 text-base",
}

const ICON_SIZES: Record<Size, string> = {
  sm: "min-h-8 min-w-8 pointer-coarse:min-h-11 pointer-coarse:min-w-11",
  md: "min-h-10 min-w-10",
  lg: "min-h-11 min-w-11",
}

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-md font-medium cursor-pointer motion-safe:transition-colors disabled:opacity-50 disabled:pointer-events-none"

export function Button(props: TextButtonProps | IconButtonProps) {
  const { variant = "secondary", size = "md", pressed, loading, iconOnly, className, type, disabled, children, ...rest } = props
  const sizeClass = iconOnly ? ICON_SIZES[size] : SIZES[size]
  const classes = [BASE, VARIANTS[variant], sizeClass, className].filter(Boolean).join(" ")
  return (
    <button
      {...rest}
      type={type ?? "button"}
      className={classes}
      disabled={disabled || loading}
      aria-pressed={pressed}
      aria-busy={loading ? true : undefined}
    >
      {children}
    </button>
  )
}

export function ButtonLink({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: React.ComponentProps<"a"> & { href: string } & Pick<BaseProps, "variant" | "size">) {
  const classes = [BASE, VARIANTS[variant], SIZES[size], className].filter(Boolean).join(" ")
  return (
    <a {...rest} className={classes}>
      {children}
    </a>
  )
}
