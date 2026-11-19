# Copy / Download / Copy-link actions for memes (design)

Status: implementation (code complete on branch worktree-card-copy-download; set to done when merged and the manual acceptance list in section 10 has been run)
Plan: docs/superpowers/plans/2026-11-20-copy-download-actions.md
Originates from: docs/superpowers/specs/2026-11-18-ui-ux-audit.md

Scope: UI/UX audit ranking item 3 (idea I8, fixes F5), the "ITEM-3 SLOT" reserved in the card action bar by `2026-11-18-design-system-and-card-redesign.md` (section 4.6). Frontend only. The owner said "go with your recommendations": every open choice is decided in section 12; section 13 lists what truly needs the owner (nothing blocking).

## 1. Goal and audience

The core job of a meme app is to get a meme into a chat in one click. Today a card has no action for that (F5); the user right-clicks the image or saves a file. Audience: the owner now (dev, sometimes opened by LAN IP over plain http), later friends on a public read-only prod site (https, phones and desktops). The design must degrade gracefully when the secure-context-only Clipboard API is absent.

Out of scope: share-preview images for pasted links (audit item 5 / I22: a pasted `/memes/:id` link shows no preview until then), the details page layout (item 7), Android.

## 2. Findings that shape the design (verified in the code)

1. **No backend change is needed.** `GET /api/images/{id}` (`Backend/app/api/images.py`, `get_image`) sets `Access-Control-Allow-Origin: *` on the file response itself, `Cache-Control: public, max-age=31536000, immutable`, `Content-Disposition: inline; filename="..."; filename*=UTF-8''...` and a content type guessed from the file name. It is a FileResponse, so the explicit header survives the prod CORS middleware (which allows only the prod origins) and Caddy passes it through. So a cross-origin `fetch(url, {mode: "cors", credentials: "omit"})` returns a readable Blob, and a canvas fed from a blob is never tainted. The route is already `READER` on prod, so no `ROUTE_POLICY` line and no `backend_api.md` change.
2. **Cross-origin in dev**: the page is `localhost:5173` (or a LAN IP) and the image URL is `VITE_BACKEND_API_URL` + `/api/images/{id}`, a different origin. An `<a download>` is ignored for cross-origin URLs (the server also says `inline`). Download therefore must fetch a Blob and use a same-origin `blob:` URL.
3. **`Meme.imageUrl` is a relative path**; always resolve through `memesApi.getImageUrl(meme)` (as `MemeCard` and `MemeDetails` do). `Meme.originalFileName` is optional.
4. **Immutable caching** means a second `fetch` of an image already shown in an `<img>` is served from the HTTP cache: re-fetching for copy/download costs no extra transfer. That is why we fetch instead of drawing the existing `<img>` onto a canvas (a non-`crossorigin` `<img>` taints the canvas).
5. `MemeCard` already has the slot (`{/* SLOT item 3 ... */}` inside the bar, before the Curator group) and `REVEAL` classes (hover, `focus-within`, `pointer-coarse`). `Button iconOnly size="sm"` is 32px, and 44px on `pointer-coarse` already (`buttonStyles.ts`). The bar currently renders only when a curator control exists (`showBar`); after this change it always renders.
6. `MemeCard` is used in six places (list, duplicates review, concept details, similar memes in details, reply results, trending), so one component in the bar covers all of them. `MemeCard` tests render without a router and without providers: the new code must need neither (no `<Link>`, toast context with a no-op default).
7. `/memes/:id` (`MemePage`) is a browsing route and is never hidden on prod, so a copied page link works for friends.
8. There is a prod-served save log, `POST /api/images/meme/{id}/saves` (popularity signal; Android calls it after a gallery save). The web must not call it in this step (D12).
9. Existing tests: `MemeCard.test.tsx` (curator/comics gating, reveal classes, a slot placeholder test "not rendered while empty" that this item flips) and `MemeDetails.test.tsx`. No toast provider or clipboard helpers exist; live regions elsewhere are local `<p role="status">`.

## 3. User-facing behaviour

### 3.1 Actions

| Action | Where | What it does |
|---|---|---|
| Copy image | card bar, details row | Puts the meme on the clipboard as `image/png`, so Ctrl/Cmd-V in Slack, Telegram, WhatsApp Web, Discord, Gmail pastes the picture. |
| Download | card bar, details row | Saves the original file (same bytes as on the server, not re-encoded) with a sensible name. |
| Copy link | card bar, details row | Copies the absolute page URL `https://host/memes/{id}`. |
| Share | card bar (only touch + supported), details row (when supported) | Opens the OS share sheet with the image file (`navigator.share({files})`). |
| Open image | details row only | `ButtonLink` to the raw image URL, `target="_blank" rel="noopener noreferrer"`. (The card image is already an `<a href="/memes/:id">`, so middle-click / open in new tab works there; no extra "open" icon on the card.) |

All are visible on prod (core reader features): no `visibility.ts` entry and no capability flag.

### 3.2 Copy image

Called synchronously from the click handler. Safari requires `navigator.clipboard.write` to start inside the user gesture and accepts an async image only if the `ClipboardItem` is constructed with a **Promise** of the blob, not after an `await`:

```ts
const item = new ClipboardItem({ "image/png": pngBlobPromise })   // built in the handler, no await before it
await navigator.clipboard.write([item])
```

`pngBlobPromise = fetchImageBlob(url).then(toPngBlob)`:
- `fetchImageBlob`: `fetch(url, { mode: "cors", credentials: "omit" })`, `response.ok` check, `response.blob()`. A small module cache (`Map<imageId, Promise<Blob>>`, at most 8 entries, FIFO eviction, entry removed on rejection) is warmed on `pointerenter` / `focus` / `touchstart` of the action bar so the click path is nearly instant and (for Share) transient user activation is not burnt on a slow fetch.
- `toPngBlob(blob)`: if `blob.type === "image/png"` return it unchanged. Otherwise decode with `createImageBitmap(blob)` (fallback `Image` + `URL.createObjectURL`, revoked on load/error), draw on a canvas of the natural size (never downscaled; pixel cap 16 MP, above it error `too_large`), `canvas.toBlob(cb, "image/png")`, reject if `toBlob` yields null, `bitmap.close()` afterwards. JPEG and WebP convert fine (WebP is rare: `ingest_validate_formats` turns it into JPEG). A converted source has no alpha, so no background fill.
- **Animated GIF.** The clipboard cannot carry an animation (browsers accept only `image/png` plus text/html for `ClipboardItem`, and chat apps read the PNG). Converting would silently paste a still first frame, worse than useless for a meme. D4: detect an animated GIF (`blob.type === "image/gif"` and more than one Graphic Control Extension `0x21 0xF9` in the bytes; pure function `isAnimatedGif(bytes)`; a one-frame GIF converts normally). For an animated GIF, "Copy image" runs the **link fallback** with the direct image URL (`memesApi.getImageUrl`, which Discord/Slack/Telegram unfurl as an animation) and says: "Animated GIF cannot be copied as an image. Image link copied; use Download or Share to keep the animation." The button label and icon do not change (the type is only known after the fetch).
- Success: "Image copied".
- Errors: `NotAllowedError` -> "Copy was blocked by the browser. Try again or use Download."; network/HTTP failure -> "Could not load the image to copy."; conversion failure -> "Could not convert the image. Use Download."; `too_large` -> "Too large to copy. Use Download." Error toasts use `role="alert"`.

### 3.3 Fallbacks when image copy is impossible

`canCopyImage()` = `window.isSecureContext && typeof ClipboardItem !== "undefined" && !!navigator.clipboard?.write`. (Firefox supports `ClipboardItem` from 127.) When false:

1. The "Copy image" button is **still shown** (stable layout; the same affordance works the day the site gets https). Its handler runs the link fallback: copies the page link (3.4) and shows the info toast "Image copy needs HTTPS or a newer browser. Link copied instead." Not silent, not an error.
2. Plain http on a LAN IP (the main case; `navigator.clipboard` is `undefined` there): `copyText` has a legacy path, a hidden `<textarea>` plus `document.execCommand("copy")` inside the gesture, which still works over http. If both fail: error toast "Could not copy. Use Download, or copy the link from the address bar."
3. `localhost` and `127.0.0.1` are secure contexts, so the owner's normal dev use gets the full feature; only LAN-IP / `--host` sessions fall back.

### 3.4 Copy link

`memePageUrl(id) = new URL("/memes/" + id, window.location.origin).href` (page origin, not API origin). `copyText(url)`: `navigator.clipboard.writeText` in a secure context, else the `execCommand` path. Toast "Link copied".

### 3.5 Download

`downloadBlob(blob, filename)`: `fetchImageBlob(url)` (same cache), `URL.createObjectURL(blob)`, an `<a href=blobUrl download=filename>` appended, `click()`, removed, `setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000)` (revoking at once breaks the download in some browsers). The file is the original bytes. If `fetch` fails, last resort `window.open(url, "_blank", "noopener")` plus toast "Could not download directly; opening the image in a new tab. If nothing opens, allow pop-ups."

`buildFileName(meme, blobType)`:
1. `originalFileName` if present: last path segment only, `\ / : * ? " < > |` and control characters replaced by `_`, bidi override and directional marks (U+200E, U+200F, U+202A-202E, U+2066-2069) removed, trimmed, trailing dots and spaces removed, a reserved Windows device name (`con`, `prn`, `aux`, `nul`, `com1`-`com9`, `lpt1`-`lpt9`, with or without extension) prefixed with `_`, capped at 120 characters; if it has no extension, append one from the blob type.
2. Else `meme-{first 8 chars of id}.{ext}`.
3. `ext` from the type: `image/jpeg` -> `jpg`, png, gif, webp; unknown -> `jpg`.

iOS Safari honours `download` on blob URLs (13+) and saves to Files; on phones Share is the better path, and the bar shows it.

### 3.6 Share (optional enhancement)

`canShareFiles()` = `typeof navigator.share === "function" && typeof navigator.canShare === "function"`, then per click `navigator.canShare({ files: [file] })` with `file = new File([blob], filename, { type: blob.type })`. The card shows Share only when the first check passes **and** `matchMedia("(pointer: coarse)").matches`; the details row shows it whenever the first check passes (desktop Safari/Edge/Chrome on Windows also have a share sheet). `AbortError` (sheet dismissed) is silent. Other errors: toast "Could not share." `navigator.share` needs transient activation (Safari is strict), so the blob comes from the warmed cache and the handler awaits at most an already-started promise.

### 3.7 Feedback

Two layers, both required:
1. **On the control**: the icon swaps to a check (`CheckIcon`) for 1.5 s; the button keeps focus. Busy state while fetching via `Button loading` (`aria-busy`, disabled), so a double click cannot start two copies.
2. **A non-blocking toast** in one app-level live region: `ToastProvider` + `useToast()` in `src/ui/Toast.tsx`, mounted once in the app shell. Bottom-centre, `safe-area-inset-bottom` aware, max 3 stacked, success/info auto-dismiss 2.5 s, error 6 s with a focusable dismiss button. Two permanently mounted containers: `role="status"` (`aria-live="polite"`) for success/info and `role="alert"` for errors, both rendered empty at provider mount (a live region must exist before text is injected, otherwise the first message is not announced). Auto-dismiss pauses on hover/focus (WCAG 2.2.1), and transitions are `motion-safe:` only. `useToast()` without a provider returns a no-op, so existing card tests keep passing; then the on-control tick is the only feedback; tests assert the toast text.

Why toast plus tick rather than inline text: the card bar is hover-revealed and tiny (a "Copied" label would resize or be clipped), and it disappears on mouse-out while the toast survives. The existing `CardComicsAction` compact strip stays as is.

## 4. Components, hook, util

```
src/memeActions/
  clipboard.ts      canCopyImage(), copyText(text), writeImageToClipboard(blobPromise)
  imageBlob.ts      fetchImageBlob(url), toPngBlob(blob), isAnimatedGif(bytes), warmImageBlob(id, url)
  filename.ts       buildFileName(meme, mime), extensionForMime(mime)
  links.ts          memePageUrl(id)
  download.ts       downloadBlob(blob, filename)
  share.ts          canShareFiles(), shareImage(file)
  useMemeActions.ts hook (below)
src/ui/Toast.tsx    ToastProvider, useToast (exported from src/ui/index.ts)
src/ui/icons.tsx    + CopyIcon, DownloadIcon, LinkIcon, ShareIcon, ExternalIcon (same Icon wrapper)
src/components/MemeActions.tsx   the buttons, two layouts
```

Hook:

```ts
type ActionKind = "copyImage" | "copyLink" | "download" | "share"
function useMemeActions(meme: Meme, memesApi: MemesApi): {
  run: (kind: ActionKind) => void   // never throws; reports through the toast
  busy: ActionKind | null
  done: ActionKind | null           // set 1.5 s after success (drives the check icon)
  message: string                   // last status text (tests, no-provider use)
  warm: () => void                  // prefetch the blob (pointerenter / focus / touchstart)
  canShare: boolean
}
```

Utils return a discriminated union, `{ kind: "copied" | "fallback-link" | "cancelled" } | { kind: "error"; reason: "blocked" | "network" | "convert" | "too_large" | "unsupported" }`, and the hook maps results to strings in one table. Effects must survive a StrictMode double mount (project rule): the only effect is the `done` reset timer, cleaned on unmount; nothing starts in an effect.

`MemeActions` props: `{ meme, memesApi, layout: "bar" | "row" }`.
- `bar`: `<div role="group" aria-label="Share actions" className="flex items-center gap-1">` with icon-only `Button size="sm" variant="ghost"` (same classes as the curator buttons: `text-white! hover:bg-white/20`), names "Copy image", "Download", "Copy link", and "Share" (only `canShare` and coarse pointer). Order: Copy image, Download, Copy link; Share first on touch. `title` mirrors the name.
- `row`: `<div role="group" aria-label="Share actions" className="flex flex-wrap gap-2">` with text `Button variant="secondary"` + icon and visible labels, plus the "Open image" `ButtonLink`.

## 5. Placement and wireframes

Card, bar top-right, revealed on hover / focus-within, always on touch, `bg-scrim` backing (unchanged):

```
+---------------------------------------------+
| [Flagged]  [copy][dl][link] | [flag][comics] |   share group, then curator group (dev only)
|                                             |
|                  IMAGE                      |
|                                      [OCR]  |
+---------------------------------------------+
| #tag1 #tag2 +3                              |
+---------------------------------------------+
            ( Image copied )                      toast, bottom-centre of the viewport
```

On prod the bar holds only the share group (3 icons, plus Share on a phone). Widths: 3 x 32 px about 108 px on the narrowest desktop cell (about 190 px); with curator controls on dev 5 x 32 about 170 px, still inside the image; on touch 4 x 44 = 176 px in a one-column phone grid. A thin divider (`border-l border-white/30 pl-1`) only when both groups render.

Details (`MemeDetails`, shared by `MemeDetailsModal` and `MemePage`): a labelled row **directly under the zoomable image, above the tags block**, so it is on the first screen (F6), outside the pan/zoom container:

```
[ image ............................................ ]
[ Copy image ] [ Share ] [ Download ] [ Copy link ] [ Open image ]
#tags ...
```

## 6. Accessibility

- Every control has an accessible name (aria-label for icon-only, visible text for the row); icons are `aria-hidden` (shared `Icon` already). The check-icon swap does not change the name; the toast carries the message.
- Keyboard: native `<button>`s in DOM order; the bar stays in the DOM at `opacity-0` so Tab reaches it and `group-focus-within:opacity-100` reveals it (existing contract). Enter/Space activate; no custom key handling.
- Touch targets: icon-only `size="sm"` is already `pointer-coarse:min-h-11 min-w-11` (44 px); row buttons are `md` (40 px) with `pointer-coarse:min-h-11`; desktop minimum 32 px (above WCAG 2.5.8's 24).
- Announcements: success `role="status"`/polite, errors `role="alert"`, both containers pre-mounted; busy via `aria-busy`.
- Contrast: white icons on `bg-scrim` (existing 3:1 rule); toast text 4.5:1; focus ring from the shared Button.
- Reduced motion: no animation on the icon swap; toast transition `motion-safe:` only.

## 7. Analytics and metrics

None. No metrics or save call, no logging of meme ids, no batch metric. The save log (`POST /api/images/meme/{id}/saves`) is **not** called (D12): it would change what the popularity ranking means and needs the `searchId` plumbing. A follow-up, not in scope.

## 8. Backend

No change (finding 2.1). Preconditions to confirm in the manual check: the copy works cross-origin from the dev frontend, and from a prod build behind Caddy (`deploy/prod/edge/Caddyfile` has no rule removing `Access-Control-Allow-Origin`; when prod serves frontend and API on one origin the header is irrelevant).

## 9. Tests (vitest, jsdom)

jsdom lacks `ClipboardItem`, canvas `toBlob`, `createImageBitmap`, `URL.createObjectURL`, `navigator.share`; tests stub them with `vi.stubGlobal` / `Object.defineProperty` and restore in `afterEach`. No test touches the network or the real clipboard.

`src/memeActions/imageBlob.test.ts`
- `fetchImageBlob` calls `fetch` with `{ mode: "cors", credentials: "omit" }`, returns the blob, throws on `!ok`; the warm cache dedupes concurrent calls and evicts a rejected entry and the oldest beyond 8.
- `toPngBlob`: PNG passes through (same object, no canvas); JPEG/WebP go through mocked `createImageBitmap` + canvas `getContext("2d")`/`toBlob` and resolve `image/png`; `toBlob(null)` rejects `convert`; over 16 MP rejects `too_large`; `bitmap.close` called.
- `isAnimatedGif`: one frame false, two Graphic Control Extensions true, non-GIF false.

`src/memeActions/clipboard.test.ts`
- `canCopyImage` false when not secure, when `ClipboardItem` undefined, when `navigator.clipboard` undefined; true otherwise.
- `writeImageToClipboard` calls `navigator.clipboard.write` with one `ClipboardItem` whose `image/png` value is the **promise** (assert the stub received a promise and that `write` was called before the promise settled: the Safari contract).
- `copyText` uses `writeText` when secure, the `execCommand("copy")` textarea path otherwise (textarea removed afterwards); both failing returns an error result.

`src/memeActions/filename.test.ts`: original name kept; path segments and illegal characters stripped; missing extension added from the type; 120 cap; no name -> `meme-<8 chars>.jpg`; mime table.

`src/memeActions/download.test.ts`: `createObjectURL` mocked; one `<a download=name href=blob:...>` clicked and removed; `revokeObjectURL` after the timer (fake timers); fetch failure falls back to `window.open`.

`src/memeActions/useMemeActions.test.tsx` (renderHook, fake `MemesApi.getImageUrl`, with and without `ToastProvider`)
- copyImage success: message "Image copied", `done` set then cleared after 1.5 s.
- no `ClipboardItem` / insecure: copies the page link, message says "Link copied instead", not an error.
- animated GIF: copies the direct image URL, message explains.
- `NotAllowedError` -> blocked message; fetch rejection -> network message; both in `role="alert"`.
- download success and failure; `busy` blocks a second `run`.
- share: `canShare` false when `navigator.share` missing; `AbortError` silent; success calls `navigator.share` with a `File` of the right name and type.
- survives `<StrictMode>` double mount; timers cleaned on unmount.

`src/ui/Toast.test.tsx`: both live regions present and empty at mount; success in `status`, error in `alert`; auto-dismiss (fake timers); pause on hover; max 3; `useToast` without a provider is a no-op.

`src/components/MemeActions.test.tsx`: both layouts render the right accessible names; bar `role="group"` named "Share actions"; Share absent without `navigator.share`, absent on a fine pointer in the bar but present in the row; buttons focusable (no `tabIndex=-1`); "Open image" has `target=_blank` and `rel` with `noopener`; Copy image click calls `navigator.clipboard.write` once.

`MemeCard.test.tsx` (small): the slot placeholder test flips to "share group renders; with no curator feature the bar still renders (prod: no Flag, no comics)"; reveal classes still present; curator/comics gating tests unchanged. `MemeDetails.test.tsx`: the row is present with its named buttons.

## 10. Acceptance criteria

Automated (from `Frontend/memes-frontend/`): `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` pass; `git diff Frontend/memes-frontend/src/types/generated/` empty; no backend file changed.

Manual (implementer, on a free port per `environments/Environments.md`, never the occupied environment ports):
1. Chrome desktop, secure origin: hover a card, Copy image, paste into a chat box: the picture appears (JPEG and PNG sources). A screen reader announces "Image copied" once.
2. Download saves the original name; bytes equal the served file (compare hash).
3. Copy link pastes `https://<host>/memes/<id>`.
4. Safari macOS and iOS: Copy image works from one tap (no `NotAllowedError`); Share opens the sheet with the image attached.
5. Firefox 127+: Copy image works; an older Firefox gets the link fallback and the explanatory toast.
6. Dev frontend opened by LAN IP over http: Copy image shows the "needs HTTPS ... link copied instead" toast and the link is on the clipboard; Download works.
7. Animated GIF: Copy image copies the image link and says why; Download keeps the animation.
8. Keyboard only: Tab reaches and reveals the bar, Enter triggers each action, focus stays on the button.
9. 390 px touch: bar always visible, targets at least 44 px, Share present on a phone.
10. `VITE_STAGE=prod` build: only the share group in the bar; dev shows both groups.
11. Details modal and page show the labelled row on the first screen.
12. Network tab: at most one image request per action (the rest from cache); no call to a `/saves` or metrics endpoint.

## 11. Files to touch (keep the MemeCard diff small; another worktree edits comics UI near MemeCard)

New: everything under `src/memeActions/` with tests, `src/ui/Toast.tsx` (+ test), `src/components/MemeActions.tsx` (+ test).
Edit:
- `src/ui/icons.tsx` (five icons), `src/ui/index.ts` (export them, `ToastProvider`, `useToast`).
- `src/components/MemeCard.tsx`, about 6 lines: import `MemeActions`; replace the slot comment with `<MemeActions meme={meme} memesApi={memesApi} layout="bar" />`; drop `showBar` and render the bar unconditionally (curator controls keep their own gates). Do not reformat anything else; do not touch `CardComicsAction`, `copyId` or the cluster strip. If the comics worktree changed the bar by merge time, keep its changes and insert the single `MemeActions` line.
- `src/components/MemeDetails.tsx`: one `<MemeActions layout="row" />` under the image container.
- App shell (`App.tsx` or the routes layout): wrap in `ToastProvider` once.
- `MemeCard.test.tsx`, `MemeDetails.test.tsx` as in section 9.
- On completion: card spec section 4.6 note and the audit's Progress section ("item 3 done").
Not touched: `backend_api.md`, `Backend/**`, `shared/schemas`, `visibility.ts`, `FeatureVisibility.kt`, `stage_policy.py`.

## 12. Decisions (all defaulted to the recommendation)

- D1 Client-side canvas conversion with the promise form of `ClipboardItem`; no backend PNG endpoint (Approach A).
- D2 Fetch the original with CORS and rely on the browser cache; never draw the page's `<img>` onto the canvas.
- D3 Non-PNG sources convert to PNG at natural size, never downscaled; 16 MP cap.
- D4 Animated GIF: "Copy image" copies the direct image link with an explanatory toast; Download and Share keep the animation.
- D5 Insecure context or no `ClipboardItem`: the button stays, copies the page link and says so (info toast); legacy `execCommand` for text over http.
- D6 Download is fetch-to-Blob plus a same-origin blob anchor (the `download` attribute is ignored cross-origin); original bytes; name from `originalFileName` (sanitised), else `meme-<8 chars of id>.<ext>`.
- D7 The copied link is the page URL `/memes/:id` on the page origin, not the API image URL.
- D8 Web Share with files is optional: card shows it only on coarse pointers, details row whenever supported; `AbortError` silent.
- D9 Feedback: on-control check icon plus one app-level toast with pre-mounted polite and assertive regions; no per-card inline text; `useToast` is a no-op without a provider.
- D10 Bar order Copy image, Download, Copy link (Share first on touch), then the curator group; the bar always renders (prod shows only the share group).
- D11 Visible on prod; no `visibility.ts` or capability entry.
- D12 No analytics and no call to `/saves`. Counting a web copy/download as a save (with `searchId`) is a separate explicit follow-up because it feeds popularity.
- D13 No separate "open in new tab" icon on cards (the image is already an `<a>`); "Open image" only in the details row.
- D14 Toast lives in `src/ui` (design system), action logic in `src/memeActions/`, so the lightbox (item 7) and the reply page can reuse it.

## Decisions (owner-approved 2026-11-20)

The owner approved this spec with every recommended default (D1 to D14) and no open questions. The optional "count a web copy or download as a popularity save" is decided: no (D12 stands; any such change is a separate explicit follow-up).

## 13. Owner questions

None blocking. The backend does not have to change. One optional later decision, with the default already applied: whether a web copy or download should count as a popularity "save" (default no, D12).

## 14. Approaches considered

**A. Client canvas conversion (recommended).** Fetch the CORS-readable original, convert to PNG in a canvas where needed, write with a promise-valued `ClipboardItem`. No server work, works on dev and prod, no extra server CPU or storage, one tested util. This is the documented cross-browser pattern (web.dev "Async Clipboard API", Safari's promise form) and what image tools on the web do for "Copy image". Cons: conversion cost on huge images (capped), the clipboard cannot hold animations, secure context required for the image form.

**B. Backend PNG endpoint** (`GET /api/images/{id}/png`, Pillow, cached). Removes the canvas and the 16 MP cap and could render a GIF's first frame server-side. Cons: a new route needing a `ROUTE_POLICY` line, `backend_api.md` and tests, extra CPU or disk on a small prod VPS, a second representation to cache; it does not remove the secure-context or promise-in-gesture constraints, which are the real ones. Reconsider only if large or oddly encoded images prove common.

**C. Download and Copy link only.** Smallest, but the one-click "paste into the chat" job is exactly what is missing. Rejected.

Feedback: **toast plus on-control tick (recommended)** versus inline text only (needs room on a hover-revealed bar, vanishes with the bar on mouse-out, not announced without a live region per card) versus native `alert` dialogs (blocking, rejected). Material, GitHub and Slack confirm "Copied" with a transient polite live-region toast; the tick covers users who never look at the bottom edge.

Placement: **icon bar on cards plus a labelled row in details (recommended)** versus an overflow menu (adds a click to the one-click job and needs a menu primitive; rejected for the card in the card spec) versus a persistent footer row on every card (brings back the clutter F4 removed).

## 15. Risks and notes for the implementer

- Safari: never `await` before constructing the `ClipboardItem` in the click handler; the section 9 test (write called before the blob promise resolves) keeps this honest.
- Enterprise Chrome policies can block clipboard writes; that surfaces as `NotAllowedError` and gets the blocked message.
- Every `createObjectURL` has a matching revoke, including the `Image` fallback path in `toPngBlob`.
- The warm cache is capped at 8 blobs with FIFO eviction, so a long session does not accumulate memory.
- A pasted `/memes/:id` link has no preview until audit item 5 lands; mention it in that item's spec.
