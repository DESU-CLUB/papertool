const BUTTON_CLASS = "papertool-inline-btn";
const WRAP_CLASS = "papertool-inline-wrap";
const STATUS_CLASS = "papertool-inline-status";

const PAPER_HOST_HINTS = [
  "arxiv.org",
  "doi.org",
  "openreview.net",
  "aclanthology.org",
  "proceedings.mlr.press",
  "proceedings.neurips.cc",
  "papers.nips.cc",
  "ieeexplore.ieee.org",
  "dl.acm.org",
  "link.springer.com",
  "nature.com",
  "science.org",
  "biorxiv.org",
  "medrxiv.org",
];

function normalizeCandidateUrl(rawUrl) {
  if (!rawUrl) return "";
  try {
    const parsed = new URL(rawUrl, window.location.href);

    // Google Search redirect links: /url?q=<target>
    if (parsed.hostname === "www.google.com" && parsed.pathname === "/url") {
      const q = parsed.searchParams.get("q");
      if (q) {
        return normalizeCandidateUrl(q);
      }
    }

    return parsed.toString();
  } catch {
    return "";
  }
}

function cleanTitle(rawTitle) {
  if (!rawTitle) return document.title;
  const compact = rawTitle.replace(/\s+/g, " ").trim();
  return compact.replace(/^(title|abstract)\s*:\s*/i, "").trim();
}

function canonicalizeArxivId(rawId) {
  if (!rawId) return null;
  const normalized = rawId
    .trim()
    .replace(/^arxiv:/i, "")
    .replace(/v\d+$/i, "")
    .toLowerCase();
  return normalized || null;
}

function extractArxivId(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    if (!host.includes("arxiv.org")) return null;

    const path = parsed.pathname;
    let raw = "";
    if (path.startsWith("/abs/")) raw = path.slice("/abs/".length);
    else if (path.startsWith("/pdf/")) raw = path.slice("/pdf/".length);
    else return null;

    raw = raw.replace(/\.pdf$/i, "").replace(/^\/+|\/+$/g, "");
    return canonicalizeArxivId(raw);
  } catch {
    return null;
  }
}

function isPaperLikeUrl(url) {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    const path = parsed.pathname.toLowerCase();

    if (path.endsWith(".pdf")) return true;
    if (host.includes("arxiv.org") && (path.startsWith("/abs/") || path.startsWith("/pdf/"))) return true;
    if (host.includes("doi.org")) return true;
    if (PAPER_HOST_HINTS.some((hint) => host.includes(hint))) return true;

    return false;
  } catch {
    return false;
  }
}

function attachStatus(targetWrap) {
  let status = targetWrap.querySelector(`.${STATUS_CLASS}`);
  if (!status) {
    status = document.createElement("span");
    status.className = STATUS_CLASS;
    targetWrap.appendChild(status);
  }
  return status;
}

function setStatus(targetWrap, text, isError = false) {
  const status = attachStatus(targetWrap);
  status.textContent = text;
  status.classList.toggle("error", isError);
  if (text) {
    window.setTimeout(() => {
      if (status.textContent === text) status.textContent = "";
    }, 3000);
  }
}

function parse2DMatrix(transformValue) {
  if (!transformValue || transformValue === "none") return null;

  const matrixMatch = transformValue.match(/^matrix\(([^)]+)\)$/);
  if (!matrixMatch) return null;

  const parts = matrixMatch[1].split(",").map((part) => Number(part.trim()));
  if (parts.length !== 6 || parts.some((value) => Number.isNaN(value))) return null;

  const [a, b, c, d] = parts;
  return { a, b, c, d };
}

function applyCounterTransformIfNeeded(wrap, target) {
  let node = target;
  while (node && node !== document.body) {
    const matrix = parse2DMatrix(window.getComputedStyle(node).transform);
    if (matrix) {
      const det = matrix.a * matrix.d - matrix.b * matrix.c;
      if (Math.abs(det) > 1e-8) {
        const invA = matrix.d / det;
        const invB = -matrix.b / det;
        const invC = -matrix.c / det;
        const invD = matrix.a / det;

        // Compensate orientation flips/rotations inherited from transformed result containers.
        wrap.style.transformOrigin = "center center";
        wrap.style.transform = `matrix(${invA}, ${invB}, ${invC}, ${invD}, 0, 0)`;
      }
      return;
    }
    node = node.parentElement;
  }
}

function createCaptureButton({ getUrl, getTitle, target }) {
  if (!target || target.querySelector(`.${BUTTON_CLASS}`)) return;

  const wrap = document.createElement("span");
  wrap.className = WRAP_CLASS;

  const button = document.createElement("button");
  button.className = BUTTON_CLASS;
  button.type = "button";
  button.textContent = "Save to PaperTool";

  button.addEventListener("click", () => {
    const url = getUrl();
    const title = getTitle(url);
    if (!url) {
      setStatus(wrap, "No URL found", true);
      return;
    }

    button.disabled = true;
    setStatus(wrap, "Saving...");

    chrome.runtime.sendMessage(
      {
        type: "papertool_capture",
        url,
        title,
      },
      (response) => {
        button.disabled = false;

        if (chrome.runtime.lastError) {
          setStatus(wrap, chrome.runtime.lastError.message || "Extension error", true);
          return;
        }
        if (!response?.ok) {
          setStatus(wrap, response?.error || "Capture failed", true);
          return;
        }

        setStatus(wrap, "Saved");
      }
    );
  });

  wrap.appendChild(button);
  target.appendChild(wrap);
  applyCounterTransformIfNeeded(wrap, target);
}

function decorateArxivSearchResults() {
  const items = Array.from(document.querySelectorAll("li.arxiv-result"));
  for (const item of items) {
    const titleLine = item.querySelector("p.title");
    const link = item.querySelector("p.title a[href], a[href*='/abs/']");
    if (!titleLine || !link) continue;

    const normalizedUrl = normalizeCandidateUrl(link.href);
    if (!isPaperLikeUrl(normalizedUrl)) continue;

    createCaptureButton({
      getUrl: () => normalizedUrl,
      getTitle: (url) => extractArxivId(url) || cleanTitle(titleLine.textContent || document.title),
      target: titleLine,
    });
  }
}

function decorateArxivAbsPage() {
  const titleEl = document.querySelector("h1.title");
  if (!titleEl) return;

  const normalizedUrl = normalizeCandidateUrl(window.location.href);
  if (!isPaperLikeUrl(normalizedUrl)) return;

  createCaptureButton({
    getUrl: () => normalizedUrl,
    getTitle: (url) => extractArxivId(url) || cleanTitle(titleEl.textContent || document.title),
    target: titleEl,
  });
}

function decorateGoogleSearchResults() {
  const titleNodes = Array.from(document.querySelectorAll("#search a h3, #rso a h3"));
  for (const h3 of titleNodes) {
    const anchor = h3.closest("a[href]");
    if (!anchor) continue;

    const normalizedUrl = normalizeCandidateUrl(anchor.href);
    if (!isPaperLikeUrl(normalizedUrl)) continue;

    const target = anchor.parentElement || h3.parentElement;
    if (!target) continue;

    createCaptureButton({
      getUrl: () => normalizedUrl,
      getTitle: (url) => extractArxivId(url) || cleanTitle(h3.textContent || document.title),
      target,
    });
  }
}

function decorateGoogleScholarResults() {
  const items = Array.from(document.querySelectorAll(".gs_ri"));
  for (const item of items) {
    const titleRow = item.querySelector(".gs_rt");
    const link = item.querySelector(".gs_rt a[href]");
    if (!titleRow || !link) continue;

    const normalizedUrl = normalizeCandidateUrl(link.href);
    if (!isPaperLikeUrl(normalizedUrl)) continue;

    createCaptureButton({
      getUrl: () => normalizedUrl,
      getTitle: (url) => extractArxivId(url) || cleanTitle(titleRow.textContent || document.title),
      target: titleRow,
    });
  }
}

function run() {
  decorateArxivSearchResults();
  decorateArxivAbsPage();
  decorateGoogleSearchResults();
  decorateGoogleScholarResults();
}

let runScheduled = false;
function scheduleRun() {
  if (runScheduled) return;
  runScheduled = true;
  window.requestAnimationFrame(() => {
    runScheduled = false;
    run();
  });
}

run();

const observer = new MutationObserver(() => {
  scheduleRun();
});
observer.observe(document.documentElement, { childList: true, subtree: true });
