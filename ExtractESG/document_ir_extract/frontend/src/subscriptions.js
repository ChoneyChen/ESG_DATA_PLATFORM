/* A single scheduler owns all refreshes. Pages release subscriptions on leave.
 * Each resource waits for its previous refresh; hidden windows pause requests. */
const subscriptions = new Map();
let timer = null;

function schedule() {
  clearTimeout(timer);
  if (!subscriptions.size || document.hidden) { timer = null; return; }
  const idle = [...subscriptions.values()].filter((item) => !item.running);
  const delay = idle.length ? Math.max(20, Math.min(...idle.map((item) => item.nextAt - Date.now()))) : 250;
  timer = setTimeout(tick, Math.min(1000, delay));
}

function tick() {
  timer = null;
  if (!document.hidden) {
    for (const item of subscriptions.values()) {
      if (item.running || item.nextAt > Date.now()) continue;
      item.running = true;
      item.controller = new AbortController();
      Promise.resolve().then(() => item.refresh(item.controller.signal)).catch((error) => {
        if (!item.controller.signal.aborted) item.onError?.(error);
      }).finally(() => {
        item.running = false;
        item.nextAt = Date.now() + (typeof item.interval === "function" ? item.interval() : item.interval);
        schedule();
      });
    }
  }
  schedule();
}

export function subscribe(key, refresh, {interval = 2500, immediate = true, onError} = {}) {
  if (subscriptions.has(key)) return () => unsubscribe(key);
  subscriptions.set(key, {refresh, interval, onError, nextAt: Date.now() + (immediate ? 0 : typeof interval === "function" ? interval() : interval), running: false, controller: null});
  schedule();
  return () => unsubscribe(key);
}

export function unsubscribe(key) {
  const item = subscriptions.get(key);
  item?.controller?.abort("unsubscribed");
  subscriptions.delete(key);
  schedule();
}

export function refreshSubscription(key) {
  const item = subscriptions.get(key);
  if (item) { item.nextAt = 0; schedule(); }
}

export const subscriptionCount = () => subscriptions.size;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) for (const item of subscriptions.values()) item.controller?.abort("hidden");
  else for (const item of subscriptions.values()) item.nextAt = 0;
  schedule();
});
window.addEventListener("pagehide", () => {
  for (const key of subscriptions.keys()) unsubscribe(key);
});
