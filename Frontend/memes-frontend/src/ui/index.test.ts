import { describe, expect, it } from "vitest"
import * as ui from "./index"

describe("ui barrel", () => {
  it.each([
    "Button", "ButtonLink", "buttonClasses", "Badge", "Card", "Skeleton", "MemeCardSkeleton", "EmptyState",
    "FlagIcon", "ComicsIcon", "CheckIcon", "TextIcon",
    "CopyIcon", "DownloadIcon", "LinkIcon", "ShareIcon", "ExternalIcon", "ToastProvider", "useToast",
  ])("exports %s", name => {
    expect((ui as Record<string, unknown>)[name]).toBeDefined()
  })
})
