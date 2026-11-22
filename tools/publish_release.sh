#!/bin/sh
# Publish a built release (after `make release` and a local test) to the public repository
# realAllenSong/Keel: the website (site/ -> the gh-pages branch, served by GitHub Pages) and a
# GitHub Release carrying Keel.dmg (the website's download button) and Keel-macOS.zip (in-app
# updates). The source itself is pushed to main like any repository.
#
#   tools/publish_release.sh NOTES.md          # version comes from apple/build.py
#
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="realAllenSong/Keel"
NOTES="${1:?release notes file (Markdown, one line per change)}"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/apple/build.py")"
TAG="v$VERSION"
DIST="$ROOT/dist"
for f in Keel.dmg Keel-macOS.zip Keel.dmg.sha256 Keel-macOS.zip.sha256; do
  [ -f "$DIST/$f" ] || { echo "missing $DIST/$f: run make release first"; exit 1; }
done
spctl --assess --type open --context context:primary-signature "$DIST/Keel.dmg"
xcrun stapler validate "$DIST/Keel.dmg"

# The website lives on its own branch (gh-pages, served by GitHub Pages), so the source on
# main keeps its own README and docs/.
WORK="$(mktemp -d)"
trap 'rm -r "$WORK"' EXIT
git -C "$WORK" init -q -b gh-pages pub
rsync -a --delete --exclude .git --exclude README.md "$ROOT/site/" "$WORK/pub/"
touch "$WORK/pub/.nojekyll"
(
  cd "$WORK/pub"
  git add -A
  git commit -q -m "Website for Keel $VERSION"
  git push -q --force "https://github.com/$REPO.git" gh-pages
)
gh release create "$TAG" --repo "$REPO" --title "Keel $VERSION" --notes-file "$NOTES" \
  "$DIST/Keel.dmg" "$DIST/Keel-macOS.zip" "$DIST/Keel.dmg.sha256" "$DIST/Keel-macOS.zip.sha256"
echo "published $TAG: https://github.com/$REPO/releases/tag/$TAG"
echo "download: https://github.com/$REPO/releases/latest/download/Keel.dmg"
