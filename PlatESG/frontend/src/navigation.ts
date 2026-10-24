import { useCallback, useEffect, useState } from "react";
import type { View } from "./types";

const eventName = "platesg:navigation";
const views = new Set<View>([
  "landing",
  "overview",
  "entities",
  "entity",
  "metrics",
  "metric",
  "compare",
  "sources",
  "methods",
  "api",
]);

export function readLocation() {
  return new URLSearchParams(window.location.search);
}
export function currentView(params: URLSearchParams): View {
  const value = params.get("view") || "";
  if (value === "home") return "landing";
  return views.has(value as View) ? (value as View) : "landing";
}
export function updateLocation(
  values: Record<string, string | null>,
  replace = false,
) {
  const params = readLocation();
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === "") params.delete(key);
    else params.set(key, value);
  }
  const url = `${window.location.pathname}${params.size ? `?${params}` : ""}`;
  if (url !== `${window.location.pathname}${window.location.search}`) {
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
    window.dispatchEvent(new Event(eventName));
  }
}
export function useLocation() {
  const [params, setParams] = useState(readLocation);
  useEffect(() => {
    const read = () => setParams(readLocation());
    window.addEventListener("popstate", read);
    window.addEventListener(eventName, read);
    return () => {
      window.removeEventListener("popstate", read);
      window.removeEventListener(eventName, read);
    };
  }, []);
  const patch = useCallback(updateLocation, []);
  return [params, patch] as const;
}
export function navigate(
  view: View,
  values: Record<string, string | null> = {},
) {
  const url = pageHref(
    view,
    Object.fromEntries(
      Object.entries(values).filter(
        (entry): entry is [string, string] => entry[1] !== null,
      ),
    ),
  );
  if (url !== window.location.pathname + window.location.search) {
    window.history.pushState({}, "", url);
    window.dispatchEvent(new Event(eventName));
  }
  window.scrollTo?.({ top: 0, behavior: "instant" });
}
export function pageHref(view: View, values: Record<string, string> = {}) {
  const params = new URLSearchParams();
  const dataset =
    readLocation().get("dataset_version") || readLocation().get("dataset");
  if (dataset) params.set("dataset_version", dataset);
  if (view !== "landing") params.set("view", view);
  for (const [key, value] of Object.entries(values))
    if (value) params.set(key, value);
  return `${window.location.pathname}${params.size ? `?${params}` : ""}`;
}
