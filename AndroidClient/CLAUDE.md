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

## CI runs instrumented tests on API 31, not the real minSdk (29) — temporary

`minSdk = 29` in `app/build.gradle.kts`, but `.github/workflows/android-ci.yml`'s
`instrumented-tests` job runs on an **API 31** emulator, not 29. This is a
workaround, not a design choice — don't "fix" it back to 29 without checking
whether the underlying issue is resolved (see below).

**Why:** `androidx.activity:activity-compose` 1.13.0+ makes `ComponentActivity`
implement a callback referencing `android.app.PictureInPictureUiState`, a class
that only exists on API 31+. MockK's Android proxy reflectively scans all
declared methods on a class when deciding whether to intercept a call, and that
scan touches the missing class on API <31, crashing with
`ClassNotFoundException: android.app.PictureInPictureUiState` — in *any*
instrumented test that pauses an Activity while any mock exists, not just PiP
tests. Confirmed upstream MockK bug: mockk/mockk#1518. Fix merged upstream
(mockk/mockk#1531) on 2026-06-12 but not in any released mockk version as of
2026-07-21.

**Why it's low-risk right now:** this app has zero `Build.VERSION.SDK_INT`
branches anywhere in `app/src/main`, and its only permissions (`INTERNET`,
`ACCESS_NETWORK_STATE`, `REQUEST_INSTALL_PACKAGES`) don't behave differently
between API 29 and 31 — no scoped-storage, media, notification, or Bluetooth
permission changes apply here. There's no version-conditional code path for
the API-29-vs-31 gap to actually hide a bug in.

**Coverage gap this leaves:** automated instrumented (Espresso/Compose) tests
no longer run against the real minSdk floor. The `smoke-test-min-sdk` CI job
(also in `android-ci.yml`) partially covers this: it installs the real debug
APK on an API 29 emulator and confirms the app launches without crashing —
no MockK, no Espresso, just `adb install` + `adb shell am start` + a logcat
crash check. It won't catch UI-level regressions, only gross launch/install
failures on API 29 specifically.

**To revert:** once mockk ships a release containing #1531, bump the `mockk`
version in `gradle/libs.versions.toml`, change `instrumented-tests`' `api-level`
back to 29, and remove the `smoke-test-min-sdk` job (no longer needed once
instrumented tests cover 29 again).

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