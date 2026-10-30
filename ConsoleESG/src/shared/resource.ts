import { useCallback, useSyncExternalStore } from "react";
import { request, type Service } from "./api";

type Snapshot = {
  data?: unknown;
  error?: Error;
  loading: boolean;
  updatedAt?: number;
};
type Entry = {
  service: Service;
  path: string;
  snapshot: Snapshot;
  listeners: Map<() => void, number>;
  controller?: AbortController;
  pending?: Promise<void>;
  timer?: ReturnType<typeof setTimeout>;
  revision: number;
};
const entries = new Map<string, Entry>();
const idle: Snapshot = { loading: false };

function notify(entry: Entry, patch: Partial<Snapshot>) {
  entry.snapshot = { ...entry.snapshot, ...patch };
  entry.listeners.forEach((_, fn) => fn());
}
function schedule(entry: Entry) {
  clearTimeout(entry.timer);
  const intervals = [...entry.listeners.values()].filter((x) => x > 0);
  if (intervals.length)
    entry.timer = setTimeout(
      () => {
        if (document.visibilityState === "hidden") schedule(entry);
        else void load(entry);
      },
      Math.min(...intervals),
    );
}
function load(entry: Entry): Promise<void> {
  if (entry.pending) return entry.pending;
  clearTimeout(entry.timer);
  const revision = entry.revision;
  const controller = new AbortController();
  entry.controller = controller;
  notify(entry, { loading: true, error: undefined });
  entry.pending = request(entry.service, entry.path, {
    signal: controller.signal,
  })
    .then((data) => {
      if (!controller.signal.aborted && revision === entry.revision)
        notify(entry, {
          data,
          error: undefined,
          loading: false,
          updatedAt: Date.now(),
        });
    })
    .catch((error) => {
      if (!controller.signal.aborted && revision === entry.revision)
        notify(entry, {
          error: error instanceof Error ? error : new Error("读取失败"),
          loading: false,
        });
    })
    .finally(() => {
      entry.pending = undefined;
      entry.controller = undefined;
      if (
        (revision !== entry.revision || controller.signal.aborted) &&
        entry.listeners.size
      )
        void load(entry);
      else {
        if (entry.snapshot.loading) notify(entry, { loading: false });
        schedule(entry);
      }
    });
  return entry.pending;
}
function entryFor(service: Service, path: string) {
  const key = service + ":" + path;
  let entry = entries.get(key);
  if (!entry) {
    if (entries.size > 100)
      for (const [oldKey, old] of entries)
        if (!old.listeners.size && !old.pending) entries.delete(oldKey);
    entry = {
      service,
      path,
      snapshot: idle,
      listeners: new Map(),
      revision: 0,
    };
    entries.set(key, entry);
  }
  return entry;
}

export function invalidate(service?: Service, pathPrefix = "") {
  for (const entry of entries.values())
    if (
      (!service || entry.service === service) &&
      entry.path.startsWith(pathPrefix)
    ) {
      entry.revision += 1;
      if (entry.listeners.size) void load(entry);
      else entries.delete(entry.service + ":" + entry.path);
    }
}

export function useResource<T>(
  service: Service,
  path: string | null,
  interval = 0,
) {
  const entry = path ? entryFor(service, path) : undefined;
  const subscribe = useCallback(
    (listener: () => void) => {
      if (!entry) return () => {};
      entry.listeners.set(listener, interval);
      if (
        !entry.snapshot.data ||
        !entry.snapshot.updatedAt ||
        Date.now() - entry.snapshot.updatedAt > 1000
      )
        void load(entry);
      else schedule(entry);
      const visible = () => {
        if (document.visibilityState === "visible" && interval > 0)
          void load(entry);
      };
      document.addEventListener("visibilitychange", visible);
      return () => {
        entry.listeners.delete(listener);
        document.removeEventListener("visibilitychange", visible);
        if (!entry.listeners.size) {
          clearTimeout(entry.timer);
          entry.controller?.abort();
        } else schedule(entry);
      };
    },
    [entry, interval],
  );
  const snapshot = useSyncExternalStore(
    subscribe,
    () => entry?.snapshot || idle,
    () => idle,
  );
  const reload = useCallback(() => {
    if (entry) {
      entry.revision += 1;
      void load(entry);
    }
  }, [entry]);
  return { ...snapshot, data: snapshot.data as T | undefined, reload };
}
