#!/bin/zsh
set -e
cd "$(dirname "$0")"
mkdir -p LocalBooks.app/Contents/MacOS LocalBooks.app/Contents/Resources/runtime/static
cp packaging/Info.plist LocalBooks.app/Contents/Info.plist
cp packaging/AppIcon.icns LocalBooks.app/Contents/Resources/AppIcon.icns
cp app.py documents.py scan.py statements.py reconciliation.py compliance.py pdf.mjs maintenance.py LocalBooks.app/Contents/Resources/runtime/
cp -R static/. LocalBooks.app/Contents/Resources/runtime/static/
mkdir -p LocalBooks.app/Contents/Resources/runtime/ocr
cp -R ocr/tessdata LocalBooks.app/Contents/Resources/runtime/ocr/
swiftc -module-cache-path /tmp/localbooks-swift-cache launcher.swift -o LocalBooks.app/Contents/MacOS/LocalBooks
codesign --force --sign - LocalBooks.app
echo 'Built LocalBooks.app'
