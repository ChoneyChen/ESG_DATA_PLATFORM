import { useCallback, useEffect, useState } from "react";
import type { View } from "./types";

const eventName = "platesg:navigation";
export const readLocation = () => new URLSearchParams(window.location.search);
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
  const patch = useCallback(
    (values: Record<string, string | null>, replace = false) => {
      const params = readLocation();
      for (const [key, value] of Object.entries(values)) {
        if (value == null || value === "") params.delete(key);
        else params.set(key, value);
      }
      const url = location.pathname + (params.size ? "?" + params : "");
      history[replace ? "replaceState" : "pushState"]({}, "", url);
      window.dispatchEvent(new Event(eventName));
    },
    [],
  );
  return [params, patch] as const;
}
export function pageHref(_view: View, values: Record<string, string> = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value) params.set(key, value);
  return location.pathname + (params.size ? "?" + params : "");
}
export function navigate(
  view: View,
  values: Record<string, string | null> = {},
) {
  const entries = Object.fromEntries(
    Object.entries(values).filter(
      (entry): entry is [string, string] => entry[1] !== null,
    ),
  );
  history.pushState({}, "", pageHref(view, entries));
  window.dispatchEvent(new Event(eventName));
  window.scrollTo?.({ top: 0, behavior: "instant" });
}
