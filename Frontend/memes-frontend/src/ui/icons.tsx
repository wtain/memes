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

export function CopyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="7" y="7" width="9" height="10" rx="1.5" />
      <path d="M13 7V4.5A1.5 1.5 0 0 0 11.5 3h-6A1.5 1.5 0 0 0 4 4.5v8A1.5 1.5 0 0 0 5.5 14H7" />
    </Icon>
  )
}

export function DownloadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M10 3v10" />
      <path d="M6 9.5l4 4 4-4" />
      <path d="M4 17h12" />
    </Icon>
  )
}

export function LinkIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M8.5 11.5a3 3 0 0 0 4.2 0l2.6-2.6a3 3 0 0 0-4.2-4.2l-.8.8" />
      <path d="M11.5 8.5a3 3 0 0 0-4.2 0l-2.6 2.6a3 3 0 0 0 4.2 4.2l.8-.8" />
    </Icon>
  )
}

export function ShareIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="5" cy="10" r="2" />
      <circle cx="15" cy="5" r="2" />
      <circle cx="15" cy="15" r="2" />
      <path d="M6.8 9l6.4-3" />
      <path d="M6.8 11l6.4 3" />
    </Icon>
  )
}

export function ExternalIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M8 4H5.5A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16h9a1.5 1.5 0 0 0 1.5-1.5V12" />
      <path d="M11 4h5v5" />
      <path d="M16 4l-7 7" />
    </Icon>
  )
}
