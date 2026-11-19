import { BASE, ICON_SIZES, SIZES, VARIANTS, buttonClasses, type Size, type Variant } from "./buttonStyles"

type BaseProps = {
  variant?: Variant
  size?: Size
  pressed?: boolean
  loading?: boolean
}
type TextButtonProps = React.ComponentProps<"button"> & BaseProps & { iconOnly?: false }
type IconButtonProps = React.ComponentProps<"button"> & BaseProps & { iconOnly: true; "aria-label": string }

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
  const classes = buttonClasses({ variant, size, className })
  return (
    <a {...rest} className={classes}>
      {children}
    </a>
  )
}
