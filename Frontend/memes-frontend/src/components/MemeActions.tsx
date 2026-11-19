import type { MemesApi } from "../api/MemesApi"
import { useMemeActions, type ActionKind } from "../memeActions/useMemeActions"
import type { Meme } from "../types/generated/all"
import { Button, ButtonLink, CheckIcon, CopyIcon, DownloadIcon, ExternalIcon, LinkIcon, ShareIcon } from "../ui"

type Props = {
  meme: Meme
  memesApi: MemesApi
  layout: "bar" | "row"
}

const ICONS = { copyImage: CopyIcon, download: DownloadIcon, copyLink: LinkIcon, share: ShareIcon } as const
const LABELS: Record<ActionKind, string> = {
  copyImage: "Copy image",
  download: "Download",
  copyLink: "Copy link",
  share: "Share",
}

function isCoarsePointer(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches
}

export function MemeActions({ meme, memesApi, layout }: Props) {
  const { run, busy, done, warm, canShare } = useMemeActions(meme, memesApi)
  const bar = layout === "bar"

  const showShare = canShare && (!bar || isCoarsePointer())
  const kinds: ActionKind[] = bar
    ? [...(showShare ? (["share"] as const) : []), "copyImage", "download", "copyLink"]
    : ["copyImage", ...(showShare ? (["share"] as const) : []), "download", "copyLink"]

  function renderButton(kind: ActionKind) {
    const Icon = done === kind ? CheckIcon : ICONS[kind]
    const label = LABELS[kind]
    const onClick = (e: React.MouseEvent) => {
      e.stopPropagation()
      run(kind)
    }
    if (bar) {
      return (
        <Button
          key={kind}
          iconOnly
          size="sm"
          variant="ghost"
          aria-label={label}
          title={label}
          loading={busy === kind}
          className="text-white! hover:bg-white/20"
          onClick={onClick}
        >
          <Icon />
        </Button>
      )
    }
    return (
      <Button key={kind} variant="secondary" loading={busy === kind} onClick={onClick}>
        <Icon />
        {label}
      </Button>
    )
  }

  return (
    <div
      role="group"
      aria-label="Share actions"
      className={bar ? "flex items-center gap-1" : "flex flex-wrap gap-2"}
      onPointerEnter={warm}
      onFocus={warm}
      onTouchStart={warm}
    >
      {kinds.map(renderButton)}
      {!bar && (
        <ButtonLink
          href={memesApi.getImageUrl(meme)}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
        >
          <ExternalIcon />
          Open image
        </ButtonLink>
      )}
    </div>
  )
}
