import { truncateMiddle } from "../utils/truncateMiddle"

type Props = {
  name: string
  /** Longest text shown, in characters; longer names are cut in the middle. */
  max?: number
}

/** A file name that cannot stretch its row: long names are cut in the middle and keep the full name in a tooltip. */
export function FileName({ name, max = 32 }: Props) {
  const shown = truncateMiddle(name, max)
  return (
    <span
      className="inline-block max-w-[16rem] truncate whitespace-nowrap align-middle"
      title={shown !== name ? name : undefined}
    >
      {shown}
    </span>
  )
}
