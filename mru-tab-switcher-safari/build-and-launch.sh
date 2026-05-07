#!/usr/bin/env bash
# Build the Safari Web Extension wrapper from the CLI and launch it so Safari
# re-registers the extension. Equivalent to hitting Cmd+R in Xcode without
# needing the IDE open.
#
# Usage:
#   ./build-and-launch.sh             # Debug build, default scheme
#   ./build-and-launch.sh Release     # Release build

set -euo pipefail

CONFIG="${1:-Debug}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$SCRIPT_DIR/MRU Tab Switcher.xcodeproj"
SCHEME="MRU Tab Switcher (macOS)"
BUILD_DIR="$SCRIPT_DIR/build"

echo "==> Building $SCHEME ($CONFIG)"
xcodebuild \
  -project "$PROJECT" \
  -scheme "$SCHEME" \
  -configuration "$CONFIG" \
  -derivedDataPath "$BUILD_DIR" \
  -quiet \
  build

APP_PATH="$BUILD_DIR/Build/Products/$CONFIG/MRU Tab Switcher.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Build succeeded but app not found at: $APP_PATH" >&2
  exit 1
fi

# Killing any running copy first guarantees the new build registers cleanly.
osascript -e 'tell application "MRU Tab Switcher" to quit' 2>/dev/null || true
sleep 0.5

echo "==> Launching $APP_PATH"
open "$APP_PATH"

cat <<'EOF'

Built and launched. Next steps in Safari (only if extension misbehaves):

  1. Safari -> Settings -> Extensions -> toggle MRU Tab Switcher off, then on.
     This forces Safari to reload the extension's resources.
  2. To view logs: Safari -> Develop -> Web Extension Background Pages
     -> MRU Tab Switcher -> opens Web Inspector with the [mru] console.

If the extension disappeared after a Safari restart, also re-enable
"Allow unsigned extensions" in Safari -> Develop -> Developer Settings.
EOF
