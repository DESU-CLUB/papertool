# PaperTool Chrome Extension

This extension sends the current tab URL to the local PaperTool bridge API.

## Setup

1. Start bridge server in your PaperTool repo:
   - `papertool bridge --host 127.0.0.1 --port 17345`
2. Open `chrome://extensions`.
3. Enable Developer Mode.
4. Click `Load unpacked` and select this `chrome-extension/` folder.

## Usage

1. Open a page you want to capture (arXiv, GitHub, X, or any web page).
2. Click `PaperTool Capture` extension icon.
3. Confirm bridge endpoint (default `http://127.0.0.1:17345`).
4. Click `Capture Current Tab`.

The bridge imports the URL into `library/captures/` and ingests it into PaperTool.
