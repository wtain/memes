type IconProps = React.ComponentProps<"svg">

function Icon({ children, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 20 20"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

export function FlagIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 17V3" />
      <path d="M5 4h10l-2 3.5L15 11H5" />
    </Icon>
  )
}

export function ComicsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="3" width="6" height="6" rx="1" />
      <rect x="11" y="3" width="6" height="6" rx="1" />
      <rect x="3" y="11" width="14" height="6" rx="1" />
    </Icon>
  )
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 10.5l4 4 8-9" />
    </Icon>
  )
}

export function TextIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 5h12" />
      <path d="M10 5v11" />
      <path d="M7.5 16h5" />
    </Icon>
  )
}
