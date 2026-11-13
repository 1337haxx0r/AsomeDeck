#!/usr/bin/env bash
#
# Build dist/AsomeDeck-<version>.app, and sign it as far as this machine is able.
#
# Run from the repo root:
#
#     bash packaging/build_mac.sh
#
# Signing is chosen by environment, not by editing this file:
#
#   (nothing set)                   ad-hoc. PyInstaller signs the binaries itself, which on
#                                   Apple Silicon is not optional -- an unsigned arm64
#                                   binary will not execute at all. Runs on this Mac. On
#                                   someone else's, macOS 15 shows "Apple could not verify
#                                   ... is free of malware" and they must allow it once in
#                                   System Settings > Privacy & Security. Handing the app
#                                   over by USB, scp or git avoids that entirely: those do
#                                   not set the quarantine attribute that triggers it.
#
#   ASOMEDECK_CODESIGN_IDENTITY     e.g. "Developer ID Application: Your Name (TEAMID)".
#                                   Signs with the hardened runtime and a secure timestamp,
#                                   which is what notarization requires.
#
#   ASOMEDECK_NOTARY_PROFILE        a keychain profile name, created once with:
#                                     xcrun notarytool store-credentials <name> \
#                                       --apple-id <you@example.com> \
#                                       --team-id <TEAMID> --password <app-specific-password>
#                                   Set it *and* the identity above and the app is
#                                   submitted, waited on, and stapled -- after which it
#                                   opens on any Mac with no warning at all.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
# Asked of the source rather than written here, for the reason src/asomedeck/__init__.py
# gives: a second copy of a version is a copy that eventually disagrees. The spec names
# the bundle the same way, so these two cannot drift apart without the build failing here,
# loudly, on a path that does not exist.
VERSION="$("$PYTHON" -c 'import sys; sys.path.insert(0, "src"); import asomedeck; print(asomedeck.__version__)')"
APP="dist/AsomeDeck-$VERSION.app"

echo "==> building"
"$PYTHON" -m PyInstaller packaging/asomedeck-mac.spec --noconfirm

if [ -n "${ASOMEDECK_CODESIGN_IDENTITY:-}" ]; then
    echo "==> signing as $ASOMEDECK_CODESIGN_IDENTITY"
    # --deep is deprecated for signing but remains the practical way to reach the nested Qt
    # frameworks a PyInstaller bundle carries; the outer signature is applied last so it
    # covers them.
    codesign --force --deep --options runtime --timestamp \
             --entitlements packaging/entitlements.plist \
             --sign "$ASOMEDECK_CODESIGN_IDENTITY" "$APP"

    if [ -n "${ASOMEDECK_NOTARY_PROFILE:-}" ]; then
        echo "==> notarizing"
        ZIP="$(mktemp -d)/AsomeDeck-$VERSION.zip"
        # ditto, not zip: the bundle has symlinks inside its frameworks and zip(1) follows
        # them, which both bloats the upload and breaks the signature.
        ditto -c -k --keepParent "$APP" "$ZIP"
        xcrun notarytool submit "$ZIP" \
              --keychain-profile "$ASOMEDECK_NOTARY_PROFILE" --wait
        # Staples the ticket into the bundle so it validates offline, on a machine that has
        # never talked to Apple about it.
        xcrun stapler staple "$APP"
    else
        echo "==> not notarizing (ASOMEDECK_NOTARY_PROFILE unset)"
    fi
else
    echo "==> ad-hoc signature only (ASOMEDECK_CODESIGN_IDENTITY unset)"
fi

echo "==> verifying"
codesign --verify --deep --strict --verbose=2 "$APP"
echo
du -sh "$APP"
echo "built $APP"
