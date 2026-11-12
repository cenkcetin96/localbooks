# Third-party notices

The MIT license applies to LocalBooks application code. Bundled components retain their own licenses:

- **Inter fonts** (`static/fonts/`): copyright The Inter Project Authors, SIL Open Font License 1.1. Full text: [LICENSE-Inter.txt](static/fonts/LICENSE-Inter.txt). Upstream: https://github.com/rsms/inter.
- **Tesseract English and Thai trained data** (`ocr/tessdata/`): Apache License 2.0. Full text: [LICENSE-tessdata.txt](ocr/tessdata/LICENSE-tessdata.txt). Upstream: https://github.com/tesseract-ocr/tessdata_fast.
- **Kasikornbank logo** (`static/banks/kasikorn.svg`): [source and attribution](static/banks/README.md). Wikimedia Commons marks the simple logo as public domain; trademark rights remain with the bank. Used only to identify bank accounts.

The downloadable Mac app also bundles Python, Node.js, Chromium headless shell, Poppler, Tesseract and their required libraries as separate runtime components. Their licenses and copyright notices are in `LocalBooks.app/Contents/Resources/ThirdPartyNotices`, the Python runtime, and the Chromium `LICENSE.headless_shell` file. The matching release includes an archive of exact upstream sources and Homebrew build recipes. These third-party components are not relicensed as MIT. Apple's system frameworks and developer tools are not redistributed.

LocalBooks uses the PyInstaller bootloader under its GPL license with the bootloader exception permitting applications under other licenses; its license text is included in the app notices.

Bank and product names identify compatible formats or required software. LocalBooks is not affiliated with or endorsed by those vendors.
