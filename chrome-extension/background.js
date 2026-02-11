const DEFAULT_ENDPOINT = "http://127.0.0.1:17345";
const QUEUE_KEY = "papertoolCaptureQueue";
const ALARM_NAME = "papertool_drain_queue";
const LAST_UPLOAD_KEY = "papertoolLastUpload";
const BACKOFF_STEPS_MS = [30000, 60000, 120000, 240000, 480000, 900000, 1800000];

async function getEndpoint() {
  const stored = await chrome.storage.local.get(["papertoolEndpoint"]);
  const endpoint = (stored.papertoolEndpoint || DEFAULT_ENDPOINT).trim().replace(/\/$/, "");
  return endpoint || DEFAULT_ENDPOINT;
}

async function getAuthToken() {
  const stored = await chrome.storage.local.get(["papertoolAuthToken"]);
  const token = (stored.papertoolAuthToken || "").trim();
  return token || "";
}

function nowMs() {
  return Date.now();
}

function jitter(ms) {
  const ratio = 0.1;
  const delta = ms * ratio;
  const value = ms + (Math.random() * 2 - 1) * delta;
  return Math.max(1000, Math.round(value));
}

function backoffMs(attempts) {
  const idx = Math.max(0, Math.min(attempts, BACKOFF_STEPS_MS.length - 1));
  return jitter(BACKOFF_STEPS_MS[idx]);
}

async function loadQueue() {
  const stored = await chrome.storage.local.get([QUEUE_KEY]);
  const queue = stored[QUEUE_KEY];
  if (!Array.isArray(queue)) return [];
  return queue;
}

async function saveQueue(queue) {
  await chrome.storage.local.set({ [QUEUE_KEY]: queue });
}

function classifyFailure(error, statusCode = null) {
  const message = (error && error.message) || String(error || "unknown_error");
  if (statusCode === 429) {
    return { retry: true, terminal: false, reason: message };
  }
  if (statusCode !== null && statusCode >= 400 && statusCode < 500) {
    return { retry: false, terminal: true, reason: message };
  }
  return { retry: true, terminal: false, reason: message };
}

async function enqueueCapture({ url, title = "", contextText = "", sourcePage = "" }) {
  if (!url) throw new Error("URL is required");

  const queue = await loadQueue();
  const id = crypto.randomUUID();
  queue.push({
    id,
    payload: {
      request_id: id,
      url,
      title,
      context_text: contextText,
      source_page: sourcePage,
      captured_at: new Date().toISOString(),
    },
    attempts: 0,
    nextRetryAt: nowMs(),
    status: "pending",
    lastError: "",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  });
  await saveQueue(queue);
  await scheduleNextDrain(queue);
  return id;
}

async function scheduleNextDrain(queueInput = null) {
  const queue = queueInput || (await loadQueue());
  const now = nowMs();
  const pending = queue.filter((item) => item.status === "pending");
  if (!pending.length) {
    await chrome.alarms.clear(ALARM_NAME);
    return;
  }
  const nextAt = Math.min(...pending.map((item) => Number(item.nextRetryAt || now)));
  const when = Math.max(nextAt, now + 1000);
  await chrome.alarms.create(ALARM_NAME, { when });
}

async function postCapture(endpoint, path, payload, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${endpoint}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  return { response, body };
}

async function sendCapture(endpoint, entry, token) {
  // Prefer remote API route, keep local bridge fallback for compatibility.
  const first = await postCapture(endpoint, "/v1/captures", entry.payload, token);
  if (first.response.status !== 404) {
    if (!first.response.ok || !first.body.ok) {
      const err = new Error(first.body?.error || `Capture failed (${first.response.status})`);
      err.statusCode = first.response.status;
      throw err;
    }
    return first.body;
  }

  const fallback = await postCapture(endpoint, "/capture", entry.payload, token);
  if (!fallback.response.ok || !fallback.body.ok) {
    const err = new Error(fallback.body?.error || `Capture failed (${fallback.response.status})`);
    err.statusCode = fallback.response.status;
    throw err;
  }
  return fallback.body;
}

async function drainQueue({ maxItems = 10 } = {}) {
  const endpoint = await getEndpoint();
  const token = await getAuthToken();
  const queue = await loadQueue();
  const now = nowMs();

  let processed = 0;
  for (const entry of queue) {
    if (processed >= maxItems) break;
    if (entry.status !== "pending") continue;
    if (Number(entry.nextRetryAt || 0) > now) continue;

    processed += 1;
    try {
      const result = await sendCapture(endpoint, entry, token);
      entry.status = "uploaded";
      entry.updatedAt = new Date().toISOString();
      entry.lastError = "";
      entry.result = result;
      await chrome.storage.local.set({
        [LAST_UPLOAD_KEY]: {
          ok: true,
          at: entry.updatedAt,
          request_id: entry.payload?.request_id || entry.id,
          endpoint,
        },
      });
    } catch (error) {
      const statusCode = Number(error?.statusCode || 0) || null;
      const verdict = classifyFailure(error, statusCode);
      entry.attempts = Number(entry.attempts || 0) + 1;
      entry.updatedAt = new Date().toISOString();
      entry.lastError = verdict.reason;
      await chrome.storage.local.set({
        [LAST_UPLOAD_KEY]: {
          ok: false,
          at: entry.updatedAt,
          request_id: entry.payload?.request_id || entry.id,
          endpoint,
          error: verdict.reason,
        },
      });

      if (verdict.terminal) {
        entry.status = "failed";
      } else if (verdict.retry) {
        entry.status = "pending";
        entry.nextRetryAt = nowMs() + backoffMs(entry.attempts);
      }
    }
  }

  const pruned = queue.filter((item) => item.status !== "uploaded");
  await saveQueue(pruned);
  await scheduleNextDrain(pruned);

  return {
    processed,
    pending: pruned.filter((item) => item.status === "pending").length,
    failed: pruned.filter((item) => item.status === "failed").length,
  };
}

async function queueStatus() {
  const queue = await loadQueue();
  const stored = await chrome.storage.local.get([LAST_UPLOAD_KEY]);
  return {
    pending: queue.filter((item) => item.status === "pending").length,
    failed: queue.filter((item) => item.status === "failed").length,
    total: queue.length,
    lastUpload: stored[LAST_UPLOAD_KEY] || null,
    nextRetryAt: queue
      .filter((item) => item.status === "pending")
      .map((item) => Number(item.nextRetryAt || 0))
      .sort((a, b) => a - b)[0] || null,
  };
}

chrome.runtime.onInstalled.addListener(() => {
  drainQueue().catch(() => undefined);
});

chrome.runtime.onStartup.addListener(() => {
  drainQueue().catch(() => undefined);
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== ALARM_NAME) return;
  drainQueue().catch(() => undefined);
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "papertool_capture") {
    enqueueCapture({
      url: message.url,
      title: message.title,
      contextText: message.contextText,
      sourcePage: message.sourcePage,
    })
      .then(async (requestId) => {
        const outcome = await drainQueue({ maxItems: 1 });
        sendResponse({ ok: true, queued: true, request_id: requestId, queue: outcome });
      })
      .catch((error) => {
        sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
      });
    return true;
  }

  if (message?.type === "papertool_queue_status") {
    queueStatus()
      .then((status) => sendResponse({ ok: true, ...status }))
      .catch((error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }));
    return true;
  }

  if (message?.type === "papertool_drain_queue") {
    drainQueue()
      .then((status) => sendResponse({ ok: true, ...status }))
      .catch((error) => sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) }));
    return true;
  }
});
