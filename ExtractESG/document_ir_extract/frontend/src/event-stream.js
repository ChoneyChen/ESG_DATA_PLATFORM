import {serviceBase} from "./api.js";

// Durable server cursors survive disconnects; polling remains the fallback.
export function watchExecutionEvents(onEvent, onState = () => {}) {
  let source = null, closed = false, cursor = 0, base = serviceBase();
  const connect = () => {
    source?.close(); source = null;
    if (closed || document.hidden || typeof EventSource === "undefined") { onState(false); return; }
    const next = serviceBase();
    if (next !== base) { cursor = 0; base = next; }
    source = new EventSource(`${base}/api/pipeline/events/stream?after=${cursor}`);
    source.onopen = () => onState(true);
    source.onerror = () => onState(false);
    source.addEventListener("execution", event => {
      try {
        const data = JSON.parse(event.data);
        const id = Number(event.lastEventId || data.event_id);
        if (id <= cursor) return;
        cursor = id;
        onEvent(data);
      } catch (_) { onState(false); }
    });
  };
  const visibility = () => { if (document.hidden) { source?.close(); source = null; onState(false); } else connect(); };
  document.addEventListener("visibilitychange", visibility);
  document.getElementById("backend-url")?.addEventListener("change", connect);
  const close = () => { closed = true; source?.close(); document.removeEventListener("visibilitychange", visibility); document.getElementById("backend-url")?.removeEventListener("change", connect); };
  window.addEventListener("pagehide", close, {once: true});
  connect();
  return close;
}
