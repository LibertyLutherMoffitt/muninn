#!/usr/bin/env bash
# Cut a release: bump the version everywhere, build the signed Android APK,
# tag, push and publish a GitHub release with the APK (phones running Muninn
# offer it under Settings → Updates). CI then adds the Linux/Windows builds
# (.github/workflows/desktop.yml).
#
#   tools/release.sh 0.3.0 "what changed"
#
# Needs: the Muninn signing key (~/.config/muninn/android-signing.keystore or
# $MUNINN_KEYSTORE) — phones only accept updates signed with the same key —
# and `gh` logged in. Run inside `nix develop`.
set -euo pipefail
ver="${1:?usage: tools/release.sh X.Y.Z \"notes\"}"
notes="${2:-}"
cd "$(dirname "$0")/.."
key="${MUNINN_KEYSTORE:-$HOME/.config/muninn/android-signing.keystore}"
[ -f "$key" ] || { echo "signing key not found: $key" >&2; exit 1; }
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be X.Y.Z" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean" >&2; exit 1; }

sed -i -E "s/^version = \"[^\"]+\"/version = \"$ver\"/" python/pyproject.toml
sed -i -E "s/version = \"[0-9]+\.[0-9]+\.[0-9]+\";/version = \"$ver\";/" flake.nix
sed -i -E "s/^__version__ = \"[^\"]+\"/__version__ = \"$ver\"/" python/src/muninn/__init__.py
git add python/pyproject.toml flake.nix python/src/muninn/__init__.py
git commit -m "release: v$ver" -m "${notes:-Muninn $ver}"

(cd android && gradle -q :app:assembleRelease)
out="$(mktemp -d)"
cp android/app/build/outputs/apk/release/app-release.apk "$out/muninn-android.apk"

git tag -a "v$ver" -m "Muninn $ver"
git push && git push origin "v$ver"
gh release create "v$ver" "$out/muninn-android.apk" \
  --title "Muninn $ver" \
  --notes "${notes:-Muninn $ver}

Android: install muninn-android.apk once; afterwards Muninn updates itself (Settings → Updates).
Linux / Windows: standalone builds are attached by CI a few minutes after this release."
echo "released v$ver"
