# AndroidClient — dev notes for Claude

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