const DEFAULT_ENDPOINT = "http://127.0.0.1:17345";

async function getEndpoint() {
  const stored = await chrome.storage.local.get(["papertoolEndpoint"]);
  const endpoint = (stored.papertoolEndpoint || DEFAULT_ENDPOINT).trim().replace(/\/$/, "");
  return endpoint || DEFAULT_ENDPOINT;
}

async function captureToPaperTool({ url, title = "", contextText = "" }) {
  if (!url) {
    throw new Error("URL is required");
  }

  const endpoint = await getEndpoint();
  const response = await fetch(`${endpoint}/capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      title,
      context_text: contextText,
    }),
  });

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`Capture failed (${response.status})`);
  }

  if (!response.ok || !payload.ok) {
    throw new Error(payload?.error || `Capture failed (${response.status})`);
  }

  return payload.result;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "papertool_capture") {
    return;
  }

  captureToPaperTool({
    url: message.url,
    title: message.title,
    contextText: message.contextText,
  })
    .then((result) => {
      sendResponse({ ok: true, result });
    })
    .catch((error) => {
      sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
    });

  return true;
});
