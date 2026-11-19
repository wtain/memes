# Copy / Download / Copy-link Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Copy image, Download, Copy link and Share actions to every meme card (icon bar) and to the details modal and page (labelled row), with a toast plus on-control tick as feedback.

**Architecture:** Frontend only. A new `src/memeActions/` folder holds pure utilities (filename, link, blob fetch and PNG conversion, clipboard, download, share) and one hook `useMemeActions`; `src/ui/Toast.tsx` adds an app-level `ToastProvider` with two pre-mounted live regions; `src/components/MemeActions.tsx` renders the buttons in two layouts. `MemeCard` and `MemeDetails` each get a one-line insertion.

**Tech Stack:** React, TypeScript, Tailwind 4 semantic tokens, `src/ui/*`, vitest + testing-library (jsdom), pnpm.

**Spec:** `docs/superpowers/specs/2026-11-20-copy-download-actions-design.md` (read "Decisions (owner-approved 2026-11-20)" and section 12 first). Design system: `docs/superpowers/specs/2026-11-18-design-system-and-card-redesign.md`.

## Global Constraints

- Work only inside `Frontend/memes-frontend/` (paths relative to it unless prefixed `docs/`). `Backend/`, `backend_api.md`, `shared/`, `AndroidClient/`, `src/types/generated/`, `package.json`, `pnpm-lock.yaml`, `src/features/visibility.ts` are not touched (empty `git diff`).
- No new dependency. This worktree has no `node_modules`: Task 1 installs it with `--frozen-lockfile`; `pnpm-lock.yaml` must stay unchanged.
- No dev server, no ports (5173-5175, 5190-5199, 8081-8083, DB ports), no database. Tests never touch the network or the real clipboard (stub with `vi.stubGlobal` / `Object.defineProperty`, restore in `afterEach`).
- Edit with Edit/Write tools, never PowerShell Get/Set-Content.
- React Compiler ESLint rules and StrictMode: every effect survives a double mount; refs are touched only in handlers and effects; no `useMemo`/`useCallback` for performance. The hook's only effect is the `done` reset timer (cleaned on unmount); nothing starts in an effect. `ToastProvider` timers start in the `show` handler, are tracked in a ref and cleared in an effect cleanup; no state is set during render.
- Semantic tokens only (`bg-scrim`, `bg-surface`, `text-fg`), no raw colours except the existing `text-white!` bar classes copied from the curator buttons; motion only under `motion-safe:`.
- Spec constants: warm cache max 8 entries FIFO; pixel cap 16 MP; `done` tick 1.5 s; toast auto-dismiss 2.5 s (success/info) and 6 s (error), max 3 stacked; filename cap 120 chars; blob-URL revoke after 30 s; fetch options `{ mode: "cors", credentials: "omit" }`; image clipboard type `image/png`.
- No analytics, no call to `/saves` or any metrics endpoint (D12, owner-approved: web copy/download does not count as a popularity save).
- Verification set, run from `Frontend/memes-frontend`: `pnpm exec tsc -b` ; `pnpm exec eslint src/ --max-warnings 0` ; `pnpm exec vitest run`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01R83wsKu7FzDMDHH3jYGJSe
  ```

## Review Focus

- Safari contract: `navigator.clipboard.write` must be called before the blob promise settles, with a promise-valued `ClipboardItem` and no `await` before it (Task 4, Task 7).
- Animated GIF copied as image: must copy the direct image URL with an explanation, never a silent still frame; a one-frame GIF converts normally (Tasks 3, 7).
- Insecure context / no `ClipboardItem` / `navigator.clipboard` undefined (LAN IP over http): button still works via link fallback and `execCommand`, never throws (Tasks 4, 7).
- Hostile or odd `originalFileName` (`..\..\x.jpg`, `a:b*.png`, 500 chars, no extension, empty): sanitised, capped, never empty (Task 2).
- Double click, StrictMode double mount, unmount mid-action: one copy only, timers cleaned, no state update after unmount warnings (Task 7).

## Task overview and parallelism

| Task | Subject | Depends on | Parallel? |
|---|---|---|---|
| 1 | `pnpm install --frozen-lockfile` | none | first, alone |
| 2 | `filename.ts`, `links.ts` | 1 | parallel with 3, 4, 5, 6 |
| 3 | `imageBlob.ts` | 1 | parallel |
| 4 | `clipboard.ts` | 1 | parallel |
| 5 | `download.ts`, `share.ts` | 1 | parallel (imports only types from 2, 3: see Interfaces; create after 2 and 3 if types are needed) |
| 6 | icons, `Toast.tsx`, `ToastProvider` mount | 1 | parallel |
| 7 | `useMemeActions.ts` | 2, 3, 4, 5, 6 | sequential |
| 8 | `MemeActions.tsx` | 6, 7 | sequential |
| 9 | `MemeCard` action bar | 8 | sequential (shared file) |
| 10 | `MemeDetails` row (modal and page) | 8, 9 | sequential after 9 |
| 11 | Final sweep, docs notes | all | last |

Tasks 2 to 6 touch disjoint files and can be dispatched together. Tasks 9 and 10 edit shared component files and run one after another, in that order.

---

### Task 1: Install dependencies

**Files:** none (creates untracked `node_modules/`).

- [ ] **Step 1: Install.** From `Frontend/memes-frontend`, foreground, tool timeout 600000, never `run_in_background`: `pnpm install --frozen-lockfile`. Expected: exits 0.
- [ ] **Step 2: Confirm the lockfile is untouched.** `git status --short` shows nothing (node_modules is git-ignored); `git diff --stat pnpm-lock.yaml package.json` empty.
- [ ] **Step 3: Baseline.** Run the verification set; expected all pass. Record the vitest test count to compare at the end. No commit.

---

### Task 2: Filename and link utilities

**Files:**
- Create: `src/memeActions/filename.ts`, `src/memeActions/links.ts`
- Test: `src/memeActions/filename.test.ts`, `src/memeActions/links.test.ts`

**Interfaces:**
- Produces: `extensionForMime(mime: string): "jpg" | "png" | "gif" | "webp"` (unknown -> `jpg`); `buildFileName(meme: Pick<Meme, "id" | "originalFileName">, mime: string): string`; `memePageUrl(id: string): string`.

- [ ] **Step 1: Write failing tests** in `filename.test.ts`: `extensionForMime("image/jpeg")` is `jpg`, png/gif/webp map to themselves, `"application/x"` is `jpg`; `buildFileName({id:"abcdef123456", originalFileName:"cat.png"}, "image/png")` is `cat.png`; `"C:\\x\\..\\a:b*?.png"` yields only the last segment with `\ / : * ? " < > |` and control chars replaced by `_` and no path separators; `"cat"` with `image/gif` is `cat.gif`; a 300-char name is capped at 120 characters and keeps its extension; undefined, `""` and `"   "` give `meme-abcdef12.jpg` (first 8 chars of id, ext from mime). `links.test.ts`: `memePageUrl("abc")` equals `new URL("/memes/abc", window.location.origin).href`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/memeActions/filename.test.ts src/memeActions/links.test.ts`; expected FAIL (modules missing).
- [ ] **Step 3: Implement** both files per the signatures. Sanitising order: take the last segment after splitting on `/` and `\`, replace illegal characters, trim, cap at 120 keeping the extension (truncate the stem), append the mime extension when there is no `.ext`.
- [ ] **Step 4: Run** the same command; expected PASS.
- [ ] **Step 5: Commit** `feat(actions): filename and link helpers` (with the trailer).

---

### Task 3: Image blob fetch, cache, PNG conversion, GIF detection

**Files:**
- Create: `src/memeActions/imageBlob.ts`
- Test: `src/memeActions/imageBlob.test.ts`

**Interfaces:**
- Produces:
  - `class ImageActionError extends Error { reason: "network" | "convert" | "too_large" }`
  - `fetchImageBlob(url: string): Promise<Blob>` (calls `fetch(url, { mode: "cors", credentials: "omit" })`, throws `ImageActionError("network")` on rejection or `!ok`)
  - `warmImageBlob(id: string, url: string): Promise<Blob>` (module cache `Map<string, Promise<Blob>>`, max 8, FIFO eviction, entry removed on rejection, concurrent calls share one fetch)
  - `isAnimatedGif(bytes: Uint8Array): boolean` (GIF header `GIF8` and more than one `0x21 0xF9` Graphic Control Extension)
  - `toPngBlob(blob: Blob): Promise<Blob>` (PNG returned as the same object; otherwise `createImageBitmap` with fallback `Image` + `URL.createObjectURL` revoked on load and error, canvas at natural size, `toBlob(cb, "image/png")`; null result rejects `convert`; more than 16,000,000 pixels rejects `too_large`; `bitmap.close()` always called)
  - `_resetImageBlobCacheForTests(): void`

- [ ] **Step 1: Write failing tests** (mock `fetch`, `createImageBitmap`, `HTMLCanvasElement.prototype.getContext/toBlob`): fetch options exact; throws `network` on `!ok` and on rejection; two concurrent `warmImageBlob("a", url)` call `fetch` once; a rejected entry is evicted and retried; the 9th distinct id evicts the first; `toPngBlob` returns the same PNG object and never touches canvas; JPEG and WebP resolve a blob of type `image/png`; `toBlob(null)` rejects `convert`; a 4001x4001 bitmap rejects `too_large` and still closes the bitmap; `isAnimatedGif` false for one-frame GIF bytes, true for two GCEs, false for PNG bytes.
- [ ] **Step 2: Run** `pnpm exec vitest run src/memeActions/imageBlob.test.ts`; expected FAIL.
- [ ] **Step 3: Implement** per the interface. Run GIF scanning over the bytes only when `blob.type === "image/gif"` (caller's job, see Task 7).
- [ ] **Step 4: Run** the test; expected PASS.
- [ ] **Step 5: Commit** `feat(actions): image blob fetch, cache and PNG conversion`.

---

### Task 4: Clipboard utilities

**Files:**
- Create: `src/memeActions/clipboard.ts`
- Test: `src/memeActions/clipboard.test.ts`

**Interfaces:**
- Produces:
  - `type CopyResult = { kind: "copied" } | { kind: "error"; reason: "blocked" | "unsupported" }`
  - `canCopyImage(): boolean` (`window.isSecureContext && typeof ClipboardItem !== "undefined" && !!navigator.clipboard?.write`)
  - `copyText(text: string): Promise<CopyResult>` (`writeText` when `window.isSecureContext && navigator.clipboard?.writeText`, else a hidden `<textarea>` + `document.execCommand("copy")`, textarea removed in `finally`; `NotAllowedError` maps to `blocked`; both paths failing returns `unsupported`)
  - `writeImageToClipboard(blob: Promise<Blob>): Promise<CopyResult>` (builds `new ClipboardItem({ "image/png": blob })` synchronously and calls `navigator.clipboard.write([item])` before any `await`; `NotAllowedError` -> `blocked`)

- [ ] **Step 1: Write failing tests:** `canCopyImage` false when `isSecureContext` is false, when `ClipboardItem` is undefined, when `navigator.clipboard` is undefined, true otherwise. `writeImageToClipboard`: stub `ClipboardItem` as a class recording its argument; assert `write` was called once with an item whose `"image/png"` value is the same Promise instance, and that `write` was called while that promise was still pending (use a manually controlled promise). `NotAllowedError` rejection yields `{kind:"error",reason:"blocked"}`. `copyText`: uses `writeText` when secure; with `navigator.clipboard` undefined it uses `document.execCommand` (stub returns true), the textarea is gone afterwards; `execCommand` false or throwing returns `unsupported`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/memeActions/clipboard.test.ts`; expected FAIL.
- [ ] **Step 3: Implement** per the interface.
- [ ] **Step 4: Run** the test; expected PASS.
- [ ] **Step 5: Commit** `feat(actions): clipboard utilities`.

---

### Task 5: Download and share utilities

**Files:**
- Create: `src/memeActions/download.ts`, `src/memeActions/share.ts`
- Test: `src/memeActions/download.test.ts`, `src/memeActions/share.test.ts`

**Interfaces:**
- Consumes: `fetchImageBlob` (Task 3, only in the hook; these files take a blob).
- Produces:
  - `downloadBlob(blob: Blob, filename: string): void` (`URL.createObjectURL`, append `<a href download>`, `click()`, remove, `setTimeout(() => URL.revokeObjectURL(u), 30_000)`)
  - `openInNewTab(url: string): void` (`window.open(url, "_blank", "noopener")`, the last-resort fallback)
  - `canShareFiles(): boolean` (`typeof navigator.share === "function" && typeof navigator.canShare === "function"`)
  - `type ShareResult = { kind: "shared" } | { kind: "cancelled" } | { kind: "error" }`
  - `shareImage(blob: Blob, filename: string): Promise<ShareResult>` (`new File([blob], filename, {type: blob.type})`; `navigator.canShare({files:[file]})` false returns `error`; `AbortError` returns `cancelled`)

- [ ] **Step 1: Write failing tests:** download: one anchor with `download=name` and `href` the mocked blob URL is clicked and removed from the DOM; `revokeObjectURL` is not called before 30 s and is called after (fake timers). share: `canShareFiles` false without `navigator.share`, true with both; `shareImage` calls `navigator.share` with a `File` of the right name and type; `AbortError` gives `cancelled`; `canShare` false gives `error`; another rejection gives `error`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/memeActions/download.test.ts src/memeActions/share.test.ts`; expected FAIL.
- [ ] **Step 3: Implement** per the interfaces.
- [ ] **Step 4: Run** the tests; expected PASS.
- [ ] **Step 5: Commit** `feat(actions): download and share utilities`.

---

### Task 6: Icons, Toast, ToastProvider mount

**Files:**
- Modify: `src/ui/icons.tsx` (add `CopyIcon`, `DownloadIcon`, `LinkIcon`, `ShareIcon`, `ExternalIcon` using the existing `Icon` wrapper, 20x20 viewBox), `src/ui/index.ts` (export them, `ToastProvider`, `useToast`), `src/main.tsx` (wrap `<RouterProvider>` in `<ToastProvider>` inside `FeaturesProvider`)
- Create: `src/ui/Toast.tsx`
- Test: `src/ui/Toast.test.tsx`; update `src/ui/index.test.ts` if it lists exports

**Interfaces:**
- Produces: `type ToastTone = "success" | "info" | "error"`; `ToastProvider({ children })`; `useToast(): { show: (message: string, tone?: ToastTone) => void }` (default tone `success`; outside a provider returns a stable no-op).

- [ ] **Step 1: Write failing tests:** at mount both `role="status"` (`aria-live="polite"`) and `role="alert"` containers exist and are empty; `show("Image copied")` appears inside the status region; `show("x","error")` appears in the alert region with a dismiss button that removes it; success is gone after 2.5 s and error after 6 s (fake timers); hover or focus on a toast pauses its timer, leave resumes; a fourth toast drops the oldest (max 3); `useToast()` without provider returns `show` that does not throw; works under `<StrictMode>` (a single `show` renders one toast).
- [ ] **Step 2: Run** `pnpm exec vitest run src/ui/Toast.test.tsx src/ui/index.test.ts`; expected FAIL.
- [ ] **Step 3: Implement** `Toast.tsx`: bottom-centre fixed container with `pb-[env(safe-area-inset-bottom)]`, z-index above `Modal` (check `Modal.tsx` for its z-index and use one higher), `bg-surface text-fg` tokens, `motion-safe:` transitions only. Timers live in a ref map keyed by toast id, started in `show`, cleared on dismiss and in a `useEffect` cleanup on unmount; the containers render permanently, including when empty.
- [ ] **Step 4: Edit** `icons.tsx`, `index.ts`, `main.tsx` accordingly.
- [ ] **Step 5: Run** the verification set; expected PASS.
- [ ] **Step 6: Commit** `feat(ui): toast provider and action icons`.

---

### Task 7: `useMemeActions` hook

**Files:**
- Create: `src/memeActions/useMemeActions.ts`
- Test: `src/memeActions/useMemeActions.test.tsx`

**Interfaces:**
- Consumes: everything from Tasks 2 to 6 and `MemesApi.getImageUrl(meme)`.
- Produces: `type ActionKind = "copyImage" | "copyLink" | "download" | "share"` and `useMemeActions(meme: Meme, memesApi: MemesApi): { run(kind: ActionKind): void; busy: ActionKind | null; done: ActionKind | null; message: string; warm(): void; canShare: boolean }` exactly as in spec section 4. Results map to strings in one table; the strings are the spec's (section 3.2 to 3.6): "Image copied", "Link copied", "Image copy needs HTTPS or a newer browser. Link copied instead.", "Animated GIF cannot be copied as an image. Image link copied; use Download or Share to keep the animation.", "Copy was blocked by the browser. Try again or use Download.", "Could not load the image to copy.", "Could not convert the image. Use Download.", "Too large to copy. Use Download.", "Could not copy. Use Download, or copy the link from the address bar.", "Could not download directly; the image opened in a new tab.", "Could not share."; errors use tone `error`, the fallback line `info`.

Behaviour to implement (decisions the tests pin): `run("copyImage")` when `!canCopyImage()` copies `memePageUrl(meme.id)` and shows the info text. Otherwise it builds `pngPromise = warmImageBlob(id, url).then(async b => { if (b.type === "image/gif" && isAnimatedGif(new Uint8Array(await b.arrayBuffer()))) throw new AnimatedGifSignal(); return toPngBlob(b) })` and calls `writeImageToClipboard(pngPromise)` with no `await` before it; on the animated signal it copies the direct `getImageUrl(meme)` with the GIF text. `run` ignores calls while `busy !== null`. `done` is set on success and cleared 1.5 s later by the one effect. `message` always holds the last text, and `toast.show` is called with it. `warm()` calls `warmImageBlob(meme.id, url)` and swallows rejections. `download` uses `warmImageBlob`, `buildFileName(meme, blob.type)`, `downloadBlob`; on fetch failure `openInNewTab(url)` and the fallback info text. `share` is silent on `cancelled`.

- [ ] **Step 1: Write failing tests** (renderHook; fake api with `getImageUrl`; wrapper with and without `ToastProvider`; stubs from Tasks 3 to 5 via `vi.mock` of the `imageBlob`, `clipboard`, `download`, `share` modules): copyImage success sets message "Image copied", `done === "copyImage"` then `null` after 1.5 s; `write` is called before the blob promise resolves (Safari); `canCopyImage` false copies the page link and message contains "Link copied instead"; animated GIF copies the direct image URL and shows the GIF text; `blocked` error shows the blocked text and appears in `role="alert"` under the provider; network rejection shows the load text; download success calls `downloadBlob` with `buildFileName` output; download fetch failure calls `openInNewTab`; a second `run` while `busy` does nothing; `canShare` false when `navigator.share` is missing; share `AbortError` leaves message unchanged; unmounting mid-timer leaves no pending timers (`vi.getTimerCount() === 0`); rendering under `<StrictMode>` performs no work on mount (no fetch call).
- [ ] **Step 2: Run** `pnpm exec vitest run src/memeActions/useMemeActions.test.tsx`; expected FAIL.
- [ ] **Step 3: Implement** the hook per the behaviour above.
- [ ] **Step 4: Run** the test, then the verification set; expected PASS.
- [ ] **Step 5: Commit** `feat(actions): useMemeActions hook`.

---

### Task 8: `MemeActions` component

**Files:**
- Create: `src/components/MemeActions.tsx`
- Test: `src/components/MemeActions.test.tsx`

**Interfaces:**
- Consumes: `useMemeActions`, `Button`, `ButtonLink`, icons.
- Produces: `MemeActions({ meme, memesApi, layout }: { meme: Meme; memesApi: MemesApi; layout: "bar" | "row" })` (default export not used; named export).

Spec section 4 pins both layouts. `bar`: `role="group"` `aria-label="Share actions"`, icon-only `Button size="sm" variant="ghost"` with `text-white! hover:bg-white/20`, names "Copy image", "Download", "Copy link", plus "Share" only when `canShare && matchMedia("(pointer: coarse)").matches` (placed first), `title` equal to the name, `loading={busy === kind}`, the icon swaps to `CheckIcon` while `done === kind` (name unchanged). Root `onPointerEnter`, `onFocus`, `onTouchStart` call `warm`. Buttons call `e.stopPropagation()` so a click never reaches a card link. `row`: `role="group"` `aria-label="Share actions"`, `flex flex-wrap gap-2`, text `Button variant="secondary"` with icon and visible labels (Copy image, Share when `canShare`, Download, Copy link), then `ButtonLink` "Open image" to `memesApi.getImageUrl(meme)` with `target="_blank" rel="noopener noreferrer"`. No `tabIndex=-1` anywhere. Guard `matchMedia` for jsdom (absent means fine pointer).

- [ ] **Step 1: Write failing tests:** both layouts expose the named buttons; group named "Share actions" in each; Share absent without `navigator.share`; with `navigator.share`+`canShare` and `matchMedia` fine pointer, absent in `bar` but present in `row`; present in `bar` with a coarse-pointer `matchMedia` stub; no button has `tabIndex` -1; "Open image" has `target="_blank"` and `rel` containing `noopener`; clicking Copy image calls `navigator.clipboard.write` once (secure context and `ClipboardItem` stubs); a click does not bubble to a parent `onClick`; renders without a router or provider.
- [ ] **Step 2: Run** `pnpm exec vitest run src/components/MemeActions.test.tsx`; expected FAIL.
- [ ] **Step 3: Implement** the component.
- [ ] **Step 4: Run** the test; expected PASS.
- [ ] **Step 5: Commit** `feat(actions): MemeActions component`.

---

### Task 9: Card action bar

Sequential: runs after Task 8; another worktree edits comics UI near `MemeCard`, so keep the diff to about 6 lines.

**Files:**
- Modify: `src/components/MemeCard.tsx` (lines 42 and 89 to 109 region), `src/components/MemeCard.test.tsx`

**Interfaces:** Consumes `MemeActions` (Task 8).

- [ ] **Step 1: Update tests first** in `MemeCard.test.tsx`: the test at "prod: no curator controls, no badge, no ring, no empty action bar" keeps its assertions but gains: `getByRole('group', { name: 'Share actions' })` exists and has buttons Copy image, Download, Copy link; no Flag; no `Curator actions` group (the group stays gated on curator/comics). The reveal-classes test (line ~198) asserts on `Share actions` parent too (`opacity-0`, `group-hover:opacity-100`, `group-focus-within:opacity-100`, `pointer-coarse:opacity-100`); the `Curator actions` group tests are unchanged except that `getByRole('group', {name:'Curator actions'}).parentElement` is still the bar. In dev, a thin divider element renders only when both groups render. Clicking Copy image does not trigger the card's `onClick`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/components/MemeCard.test.tsx`; expected FAIL on the new assertions.
- [ ] **Step 3: Edit** `MemeCard.tsx` minimally: import `MemeActions`; delete `showBar` and render the bar `div` unconditionally; replace the SLOT comment with `<MemeActions meme={meme} memesApi={memesApi} layout="bar" />`; wrap the existing Curator group so it renders only when `showFlag || showComics`, and give it `border-l border-white/30 pl-1`. Touch nothing else (not `CardComicsAction`, `copyId`, the cluster strip).
- [ ] **Step 4: Run** `pnpm exec vitest run src/components` (whole folder: MemeCard is used by lists, details, reply); expected PASS.
- [ ] **Step 5: Commit** `feat(actions): copy, download and link actions in the card bar`.

---

### Task 10: Details modal and page row

Sequential: after Task 9.

**Files:**
- Modify: `src/components/MemeDetails.tsx`, `src/components/MemeDetails.test.tsx`

**Interfaces:** Consumes `MemeActions` (Task 8). `MemeDetails` is shared by `MemeDetailsModal` and `MemePage`, so one edit covers both.

- [ ] **Step 1: Update tests first:** `MemeDetails.test.tsx` asserts a `group` named "Share actions" with buttons "Copy image", "Download", "Copy link" and a link "Open image" (`target=_blank`); the row sits after the zoom container and before the "Tags:" block (compare `compareDocumentPosition`); the legacy "⬇ Download" anchor is gone, so exactly one control named like Download exists. Add a `MemeDetailsModal` or `MemePage` smoke assertion only if an existing test file for them already renders `MemeDetails` (extend that, do not create a new harness).
- [ ] **Step 2: Run** `pnpm exec vitest run src/components/MemeDetails.test.tsx`; expected FAIL.
- [ ] **Step 3: Edit** `MemeDetails.tsx`: insert `<MemeActions meme={meme} memesApi={memesApi} layout="row" />` (with `className`-less wrapper `div className="mb-4"`) directly after the zoom container's closing `</div>` (around line 197) and before `{/* Metadata */}`; remove the old `<a download ...>⬇ Download</a>` block (it duplicates the row and is cross-origin so it never downloaded).
- [ ] **Step 4: Run** `pnpm exec vitest run src/components src/pages`; expected PASS.
- [ ] **Step 5: Commit** `feat(actions): action row in the meme details`.

---

### Task 11: Final sweep and docs notes

**Files:**
- Modify: `docs/superpowers/specs/2026-11-18-design-system-and-card-redesign.md` (section 4.6: one line noting the slot is filled), `docs/superpowers/specs/2026-11-18-ui-ux-audit.md` (Progress: "item 3 done"), `docs/superpowers/specs/2026-11-20-copy-download-actions-design.md` (status `done` when merged)

- [ ] **Step 1: Run the verification set** from `Frontend/memes-frontend`: `pnpm exec tsc -b` ; `pnpm exec eslint src/ --max-warnings 0` ; `pnpm exec vitest run`. Expected all green, test count above the Task 1 baseline.
- [ ] **Step 2: Check scope:** `git diff --stat main...HEAD` lists only `Frontend/memes-frontend/src/**` and `docs/**`; `git diff main...HEAD -- Frontend/memes-frontend/pnpm-lock.yaml Frontend/memes-frontend/package.json Frontend/memes-frontend/src/types/generated` is empty; `grep -rn "saves" Frontend/memes-frontend/src/memeActions` finds nothing.
- [ ] **Step 3: Update the three docs** as listed. The manual acceptance list (spec section 10) is for the owner on a free port; do not run a server here.
- [ ] **Step 4: Commit** `docs: record copy/download actions as done`.
