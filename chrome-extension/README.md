# PaperTool Chrome Extension

This extension sends URLs to the local PaperTool bridge API.

After install, it adds inline `Save to PaperTool` buttons on:
- arXiv search result cards
- arXiv paper abstract pages
- Google Search results (paper-like links)
- Google Scholar results

## Setup

1. Start bridge server in your PaperTool repo:
   - `papertool bridge --host 127.0.0.1 --port 17345`
2. Open `chrome://extensions`.
3. Enable Developer Mode.
4. Click `Load unpacked` and select this `chrome-extension/` folder.

## Usage

1. Open arXiv, Google Search, or Google Scholar.
2. Click inline `Save to PaperTool` beside a paper result title.
3. Or click extension popup and use `Capture Current Tab` on any page.
4. Set bridge endpoint in popup if needed (default `http://127.0.0.1:17345`).

The bridge imports the URL into `library/captures/` and ingests it into PaperTool.
