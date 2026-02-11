const endpointInput = document.getElementById("endpoint");
const captureButton = document.getElementById("capture");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

function extractArxivId(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (!parsed.hostname.toLowerCase().includes("arxiv.org")) return null;

    let raw = "";
    if (parsed.pathname.startsWith("/abs/")) raw = parsed.pathname.slice("/abs/".length);
    else if (parsed.pathname.startsWith("/pdf/")) raw = parsed.pathname.slice("/pdf/".length);
    else return null;

    raw = raw
      .replace(/\.pdf$/i, "")
      .replace(/^\/+|\/+$/g, "")
      .replace(/^arxiv:/i, "")
      .replace(/v\d+$/i, "")
      .toLowerCase();
    return raw || null;
  } catch {
    return null;
  }
}

async function loadEndpoint() {
  const stored = await chrome.storage.local.get(["papertoolEndpoint"]);
  if (stored.papertoolEndpoint) {
    endpointInput.value = stored.papertoolEndpoint;
  }
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.style.color = isError ? "#b91c1c" : "#0f172a";
}

async function getCurrentTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function captureCurrentTab() {
  captureButton.disabled = true;
  setStatus("Capturing...");
  resultEl.textContent = "";

  try {
    const endpoint = endpointInput.value.trim().replace(/\/$/, "");
    if (!endpoint) {
      throw new Error("Bridge endpoint is required");
    }

    await chrome.storage.local.set({ papertoolEndpoint: endpoint });

    const tab = await getCurrentTab();
    if (!tab || !tab.url) {
      throw new Error("No active tab URL found");
    }
    if (tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
      throw new Error("This page cannot be captured");
    }

    const payload = await chrome.runtime.sendMessage({
      type: "papertool_capture",
      url: tab.url,
      title: extractArxivId(tab.url) || tab.title || "",
    });
    if (!payload || !payload.ok) {
      throw new Error(payload?.error || "Capture failed");
    }

    setStatus("Captured and ingested successfully.");
    resultEl.textContent = JSON.stringify(payload.result, null, 2);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setStatus(message, true);
  } finally {
    captureButton.disabled = false;
  }
}

captureButton.addEventListener("click", captureCurrentTab);
loadEndpoint().catch(() => undefined);
