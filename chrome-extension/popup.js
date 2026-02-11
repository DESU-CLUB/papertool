const endpointInput = document.getElementById("endpoint");
const captureButton = document.getElementById("capture");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

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

    const response = await fetch(`${endpoint}/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: tab.url,
        title: tab.title || "",
      }),
    });

    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `Capture failed (${response.status})`);
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
