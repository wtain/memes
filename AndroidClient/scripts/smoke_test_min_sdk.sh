#!/usr/bin/env bash
# Installs the built debug APK on a running emulator/device and verifies the
# app launches without crashing. No MockK, no Espresso -- used by CI's
# smoke-test-min-sdk job to get *some* coverage on the real minSdk (29)
# floor while instrumented tests run on API 31 (see android-ci.yml and
# AndroidClient/CLAUDE.md for why).
#
# Usage: smoke_test_min_sdk.sh <dir-containing-apk>
set -euo pipefail

APK_DIR="${1:?usage: smoke_test_min_sdk.sh <dir-containing-apk>}"
PACKAGE="com.memebrowser.app"
LAUNCH_ACTIVITY="$PACKAGE/.MainActivity"

APK_PATH=$(find "$APK_DIR" -name '*.apk' | head -n 1)
if [ -z "$APK_PATH" ]; then
    echo "No .apk found under $APK_DIR"
    exit 1
fi
echo "Installing $APK_PATH"
adb install -r "$APK_PATH"

adb logcat -c
adb shell am start -n "$LAUNCH_ACTIVITY"
sleep 5

if adb logcat -d | grep -q "FATAL EXCEPTION"; then
    echo "App crashed on launch (API 29):"
    adb logcat -d | grep -B 5 -A 30 "FATAL EXCEPTION"
    exit 1
fi

if ! adb shell pidof "$PACKAGE"; then
    echo "App process not running after launch (API 29) -- crashed or failed to start"
    exit 1
fi

echo "App launched and stayed running on API 29 -- OK"
