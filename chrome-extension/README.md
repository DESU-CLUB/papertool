# PaperTool Chrome Extension

This extension sends URLs to PaperTool and queues uploads durably.

After install, it adds inline `Save to PaperTool` buttons on:
- arXiv search result cards
- arXiv paper abstract pages
- Google Search results (paper-like links)
- Google Scholar results

## Setup

1. Start one of:
   - Local bridge: `papertool bridge --host 127.0.0.1 --port 17345`
   - Remote API: `papertool remote serve --host 0.0.0.0 --port 18443`
2. If using remote API queueing, also start worker:
   - `papertool remote worker --poll-interval-sec 5`
3. Open `chrome://extensions`.
4. Enable Developer Mode.
5. Click `Load unpacked` and select this `chrome-extension/` folder.

## Usage

1. Open arXiv, Google Search, or Google Scholar.
2. Click inline `Save to PaperTool` beside a paper result title.
3. Or click extension popup and use `Capture Current Tab` on any page.
4. Set endpoint in popup:
   - local default: `http://127.0.0.1:17345`
   - remote example: `http://<SERVER>:18443`
5. Set Bearer token in popup if remote API auth is enabled.

Queue behavior:
- Uses `chrome.storage.local` as durable queue.
- Retry backoff: 30s, 60s, 120s, 240s, 480s, 900s, 1800s (+/-10% jitter).
- Retries: network errors, `429`, `5xx`.
- Terminal failures: other `4xx`.
- Popup shows pending/failed and last upload status.
