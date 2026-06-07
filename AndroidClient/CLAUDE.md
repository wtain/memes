# AndroidClient — dev notes for Claude

## Before committing

Always run unit tests before committing to catch compilation errors and
regressions that would otherwise fail in CI:

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew :app:testDebugUnitTest --no-daemon
```

If a ViewModel constructor changes, search for all usages in both
`src/test` and `src/androidTest` and update them before committing.
A quick grep to find every affected test file:

```powershell
grep -r "ViewModel(" app/src/test, app/src/androidTest
```

## Building locally

The system default Java is JRE 8, but AGP 8.5.2 requires Java 11+.
Set `JAVA_HOME` in your shell before running Gradle — do **not** put it in
`gradle.properties` (that would leak into CI):

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"   # JDK 21
.\gradlew assembleDebug
```

Install to a connected device:
```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -r app\build\outputs\apk\debug\app-debug.apk
```

## Image loading

The backend returns `imageUrl` as a **relative path** (`/api/images/{id}`), not
an absolute URL. Coil requires an absolute URL, so callers must prepend
`http://localhost`:

```kotlin
AsyncImage(model = "http://localhost${meme.imageUrl}", ...)
```

The OkHttp application interceptor in `NetworkModule` rewrites `localhost` to
the configured server host/port at request time, so environment switching still
works correctly.

## Architecture notes

### Detail screen
`MemeDetailViewModel` fires two parallel coroutines on init — `loadMeme()` and
`loadSimilar()` — so the main image and similar-images strip load independently.
A failure in `loadSimilar` is intentionally silent (no error shown to the user).

### Similar images
Endpoint: `GET /api/images/{id}/similar` → `MemeSearchResponse`.
Rendered as a horizontal `LazyRow` of 80 dp thumbnails at the bottom of the
detail bottom sheet. Tapping a thumbnail pushes a new `detail/{id}` destination
onto the back stack.

### Navigation
All screen-to-screen navigation goes through `NavGraph.kt`. Screens receive
callbacks (`onBack`, `onNavigateToMeme`, …) and know nothing about
`NavController` directly.