# Meme Browser — Android Client

Native Android client for the Meme Browser backend API. Supports searching, browsing, and managing memes across multiple backend environments.

## Features

- Search memes with the same query syntax as the web app
- Infinite-scroll paginated results grid
- Full-screen pinch-to-zoom / pan image view
- Mark / unmark memes as flagged (persisted via API)
- Save memes to `Pictures/MemesBrowser` gallery folder (no permissions required on Android 10+)
- Share memes via the system share sheet
- Switch between backend environments (General / IT / Metal) with per-environment health indicators
- Add, edit, and remove custom backend environments
- Thumbnail and image caching (OkHttp disk cache, honours immutable server headers)

## Requirements

- Android Studio Hedgehog (2023.1.1) or newer
- Android SDK 35
- Min SDK 29 (Android 10)
- JDK 17

## Opening the project

1. Clone the repo
2. In Android Studio: **File → Open → select the `AndroidClient` folder**
3. Let Gradle sync complete
4. Run on a device or emulator

> The app uses `android:usesCleartextTraffic="true"` to allow plain HTTP connections to local backend instances.

## Tech stack

| Concern | Library |
|---|---|
| UI | Jetpack Compose + Material 3 |
| Navigation | Compose Navigation |
| State | ViewModel + StateFlow |
| HTTP | Retrofit 2 + OkHttp 3 |
| JSON | kotlinx.serialization |
| Images | Coil 2 (shares OkHttp client) |
| Zoom / pan | Telephoto |
| Persistence | DataStore Preferences |
| DI | Hilt |

## Architecture

```
app/
└── src/main/
    ├── data/
    │   ├── api/          # Retrofit service + UrlProvider (dynamic base URL)
    │   ├── model/        # DTOs (GENERATED — see below)
    │   ├── repository/   # MemeRepository, EnvironmentRepository
    │   └── store/        # DataStore helpers
    ├── di/               # Hilt modules
    ├── ui/
    │   ├── search/       # Search screen + ViewModel
    │   ├── detail/       # Meme detail screen + ViewModel
    │   ├── environment/  # Environment manager screen + ViewModel
    │   └── theme/        # Material 3 dark theme
    └── util/
        ├── MediaStoreHelper.kt   # Save to gallery via MediaStore API
        └── ShareHelper.kt        # FileProvider-based share intent
```

The base URL for all API calls is held in a singleton `UrlProvider`. An OkHttp interceptor rewrites the host on every request, so switching environments takes effect immediately without recreating Retrofit.

## Testing

### Unit tests (JVM — no device needed)

Tests for all three ViewModels using [MockK](https://mockk.io/) and [Turbine](https://github.com/cashapp/turbine) for Flow assertions.

```bash
# From AndroidClient/
gradle :app:testDebugUnitTest
# Report: app/build/reports/tests/testDebugUnitTest/index.html
```

Coverage:
| Test class | What's covered |
|---|---|
| `SearchViewModelTest` | Initial load, pagination, facet toggle, 400 ms debounce, error state, health status |
| `MemeDetailViewModelTest` | Load meme, optimistic flagged toggle, rollback on API failure, error clear |
| `EnvironmentViewModelTest` | CRUD delegation to repository, per-URL health check result |

### Instrumented tests (Compose — requires emulator or device)

Compose UI tests for all three screens. ViewModels are created directly with MockK-backed repositories — no Hilt wiring needed.

```bash
# From AndroidClient/
gradle :app:connectedDebugAndroidTest
# Report: app/build/reports/androidTests/connected/index.html
```

Coverage:
| Test class | What's covered |
|---|---|
| `SearchScreenTest` | Search bar, settings icon, grid rendering, flagged badge, cell click, text input |
| `MemeDetailScreenTest` | Back button, save/share/flag action buttons, flagged state toggle |
| `EnvironmentManagerScreenTest` | Environment list, FAB, add dialog, edit dialog, URL display |

### CI

GitHub Actions runs both jobs on every push/PR that touches `AndroidClient/**` or `shared/schemas/**`:

- **Unit tests** — `ubuntu-latest`, no emulator, ~2 min
- **Instrumented tests** — `ubuntu-latest` + KVM + API 29 x86_64 emulator, ~15 min; skipped on draft PRs

See `.github/workflows/android-ci.yml`.

## Code generation — Kotlin DTOs

Data classes in `data/model/Models.kt` are **generated** from the canonical JSON Schema files in `shared/schemas/`. Do not edit `Models.kt` by hand.

### Regenerate

```bash
# From repo root
python AndroidClient/scripts/generate_dtos.py
```

### How it works

`AndroidClient/scripts/generate_dtos.py` reads every `*.schema.json` in `shared/schemas/` (excluding the `all.schema.json` aggregator), resolves `$ref` cross-references, sorts schemas by dependency depth, and emits a single `Models.kt` with `@Serializable` data classes.

Type mapping:

| JSON Schema type | Kotlin type |
|---|---|
| `string` | `String` |
| `number` (field named `id`, `limit`, …) | `Int` |
| `number` (other) | `Float` |
| `boolean` | `Boolean` |
| `array` of `$ref T` | `List<T>` |
| `array` of `string` | `List<String>` |

Fields absent from `required[]` are emitted as nullable with `= null`.

`HealthResponse` is appended manually at the bottom of the script — it is an API-only utility type not represented in the shared schemas.

### Adding a new schema type

1. Add `yourtype.schema.json` to `shared/schemas/`
2. Reference it from `all.schema.json` if needed
3. Run the generator: `python AndroidClient/scripts/generate_dtos.py`
4. Commit both the schema and the regenerated `Models.kt`

## Default environments

| Name | Default URL |
|---|---|
| General | `http://192.168.1.41:8082` |
| IT | `http://192.168.1.41:8083` |
| Metal | `http://192.168.1.41:8081` |

These can be edited in the app's Environment Manager screen (gear icon in the search toolbar). Custom environments can also be added and deleted.