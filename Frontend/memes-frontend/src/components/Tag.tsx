type Props = {
  label: string
}

export function Tag({ label }: Props) {
  return (
    <span className="text-xs px-2 py-1 bg-gray-200 rounded-full">
      #{label}
    </span>
  )
}
