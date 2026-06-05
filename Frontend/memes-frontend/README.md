# Memes Frontend

React + TypeScript + Vite frontend for the Memes AI Search Engine. Provides semantic search, tag-based faceted filtering, infinite scroll browsing, and a meme detail view with OCR text and similar-image suggestions.

## Stack

| Tool | Purpose |
|------|---------|
| React 19 + TypeScript | UI |
| React Router 7 | Client-side routing |
| Tailwind CSS 4 | Styling |
| Vite 7 | Dev server & bundler |
| Vitest + React Testing Library | Unit & component tests |
| ESLint 9 | Linting |

## Prerequisites

- [Node.js](https://nodejs.org/) 22+
- [pnpm](https://pnpm.io/) 10+ (`corepack enable pnpm`)

## Environment

Copy or symlink the relevant env file from `../../environments/` into that directory. The file must export:

```env
VITE_BACKEND_API_URL=http://localhost:8000
```

Vite reads `.env` files from `../../environments/` relative to this directory.

## Development

```bash
pnpm install

# Start dev server (metal environment, port 5173)
pnpm dev

# Other environments
pnpm dev-gen   # general, port 5174
pnpm dev-it    # it, port 5175
```

## Testing

Tests use Vitest with jsdom and React Testing Library. All API calls are mocked via the `MemesApi` interface.

```bash
# Run all tests once
pnpm test

# Watch mode (re-runs on file changes)
pnpm test:watch

# With coverage report
pnpm test:coverage
```

### Test structure

```
src/
├── test/setup.ts                  # Global jest-dom matchers
├── utils/
│   ├── searchParams.test.ts       # URL param parsing/building
│   └── useDebounce.test.ts        # Debounce hook (fake timers)
└── components/
    ├── Tag.test.tsx
    ├── TagList.test.tsx
    ├── Modal.test.tsx
    ├── MemeCard.test.tsx
    ├── MemesList.test.tsx
    ├── MultiSelectFacet.test.tsx
    ├── FacetSidebar.test.tsx
    └── pages/SearchPage.test.tsx
```

## Linting

```bash
# Check for lint errors (warnings allowed)
pnpm lint

# Strict mode — fails on any warning (used in CI)
pnpm lint:ci
```

## Build

```bash
pnpm build          # output: dist/
pnpm preview        # serve the production build locally
```

## Docker

The frontend is packaged as a multi-stage Docker image (Node build → nginx serve).

`VITE_BACKEND_API_URL` is baked into the JavaScript bundle at build time, so it must be provided as a build argument.

```bash
docker build \
  --build-arg VITE_BACKEND_API_URL=https://api.example.com \
  -t memes-frontend .

docker run -p 8080:80 memes-frontend
```

The container serves the SPA on port 80 with nginx. Unknown routes fall back to `index.html` for client-side routing.

## CI/CD

GitHub Actions workflows live in `.github/workflows/`:

| Workflow | Trigger | Actions |
|----------|---------|---------|
| `frontend-ci.yml` | Push/PR to `main`/`develop` touching `Frontend/**` | Install → Lint → Test → Build (smoke) |
| `frontend-release.yml` | Push of tag `v*.*.*` or `frontend-v*.*.*`, or manual dispatch | Build & push Docker image to `ghcr.io` |

### Releasing

```bash
git tag -a v1.2.3 -m "Release v1.2.3"
git push origin v1.2.3
```

The release workflow publishes the Docker image to the GitHub Container Registry:

```
ghcr.io/<owner>/memes-frontend:1.2.3
ghcr.io/<owner>/memes-frontend:1.2
ghcr.io/<owner>/memes-frontend:sha-<commit>
```

Set the `VITE_BACKEND_API_URL` repository variable in **Settings → Variables → Actions** so the release build picks up the correct backend URL automatically.

### Manual dispatch

The release workflow also supports `workflow_dispatch` for ad-hoc builds where you can specify the image tag and backend URL directly from the GitHub Actions UI.

## Project structure

```
src/
├── api/
│   ├── MemesApi.ts           # Interface (used for mocking in tests)
│   └── http/HttpMemesApi.ts  # Fetch-based implementation
├── app/
│   ├── AppLayout.tsx         # Nav + outlet wrapper
│   └── router.tsx            # Route definitions + API instantiation
├── components/               # Reusable UI components
├── pages/                    # Route-level components
├── types/
│   ├── facet.ts
│   └── generated/all.d.ts    # Auto-generated from OpenAPI schema
└── utils/
    ├── searchParams.ts       # URL param helpers
    └── useDebounce.ts        # Debounce hook
```
