// Asks the local nightblock proxy for the current state every 30 s (and whenever a tab finishes loading),
// then lays an opaque panel over every tab whose host is not on the allowlist. Nothing is closed or reloaded.
const STATE_URL = "http://127.0.0.1:8899/__nightblock__/state";
let cache = { at: 0, st: null };

function globToRe(p) {
  const parts = p.split(/(\*|\?)/).map(s => s === "*" ? ".*" : s === "?" ? "." : s.replace(/[.+^${}()|[\]\\]/g, "\\$&"));
  return new RegExp("^" + parts.join("") + "$", "i");
}

async function getState() {
  if (Date.now() - cache.at < 15000) return cache.st;
  let st = null;
  try {
    const r = await fetch(STATE_URL, { cache: "no-store" });
    st = await r.json();
    st.res = (st.allow || []).map(globToRe);
    st.local = new Set((st.local || []).map(h => h.toLowerCase()));  // /etc/hosts loopback names, e.g. sleepbandit.test
  } catch (e) { st = null; }  // proxy not running => treat as open
  cache = { at: Date.now(), st };
  return st;
}

function shouldCover(st, url) {
  if (!st || st.state !== "blocked") return false;
  let host;
  try { host = new URL(url).hostname.toLowerCase(); } catch (e) { return false; }
  if (host === "localhost" || host === "::1" || host.startsWith("127.") || host.endsWith(".local") ||
      host.endsWith(".localhost") || host.endsWith(".test") || st.local.has(host)) return false;
  return !st.res.some(re => re.test(host));
}

function applyCover(cover, until) {
  const ID = "__nightblock_cover__";
  let el = document.getElementById(ID);
  if (cover) {
    document.querySelectorAll("video,audio").forEach(m => { try { m.pause(); } catch (e) {} });
    if (!el) {
      el = document.createElement("div");
      el.id = ID;
      el.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:#000;color:#777;display:flex;" +
        "align-items:center;justify-content:center;text-align:center;padding:2em;" +
        "font:18px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;";
      document.documentElement.appendChild(el);
    }
    el.textContent = "Night block until " + until + ". This tab is covered, not closed. Go to sleep.";
  } else if (el) {
    el.remove();
  }
}

async function applyToTab(tab, st) {
  if (!tab.id || !tab.url || !/^https?:/.test(tab.url) || tab.discarded) return;
  const cover = shouldCover(st, tab.url);
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: applyCover, args: [cover, st ? st.until : ""] });
  } catch (e) { /* chrome:// pages, PDF viewers, etc. */ }
}

async function sweep() {
  const st = await getState();
  const tabs = await chrome.tabs.query({});
  for (const t of tabs) await applyToTab(t, st);
}

chrome.alarms.create("sweep", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(sweep);
chrome.runtime.onInstalled.addListener(sweep);
chrome.runtime.onStartup.addListener(sweep);
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status === "complete") applyToTab(tab, await getState());
});
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try { applyToTab(await chrome.tabs.get(tabId), await getState()); } catch (e) {}
});
