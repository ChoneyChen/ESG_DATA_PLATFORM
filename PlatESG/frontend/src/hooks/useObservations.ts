import { useEffect, useRef, useState } from "react";
import { apiUrl, request } from "../api";
import { useResource } from "./useResource";
import type { Observation, ObservationResponse } from "../types";

export function useObservations(
  dataset: string,
  filters: Record<string, string> = {},
) {
  const path = apiUrl("/v1/observations", {
    ...filters,
    ...(dataset ? { dataset_version: dataset } : {}),
    limit: 200,
  });
  const resource = useResource<ObservationResponse>(path);
  const [pages, setPages] = useState<{
    key: string | null;
    items: Observation[];
    cursor: string | null;
  }>({ key: null, items: [], cursor: null });
  const [loadingMore, setLoadingMore] = useState(false),
    [moreError, setMoreError] = useState("");
  const key = useRef(path);
  key.current = path;
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    controller.current?.abort();
    setLoadingMore(false);
    setMoreError("");
    return () => controller.current?.abort();
  }, [path]);
  const rows = [
    ...(resource.data?.items || []),
    ...(pages.key === path ? pages.items : []),
  ];
  const cursor = pages.key === path ? pages.cursor : resource.data?.next_cursor;
  async function loadMore() {
    if (!cursor || !path || loadingMore) return;
    const selectedPath = path;
    const pending = new AbortController();
    controller.current = pending;
    setLoadingMore(true);
    setMoreError("");
    try {
      const response = await request<ObservationResponse>(
        apiUrl("/v1/observations", {
          ...filters,
          dataset_version: dataset,
          limit: 200,
          cursor,
        }),
        { signal: pending.signal },
      );
      if (key.current === selectedPath && !pending.signal.aborted)
        setPages((previous) => ({
          key: selectedPath,
          items: [
            ...(previous.key === selectedPath ? previous.items : []),
            ...response.items,
          ],
          cursor: response.next_cursor,
        }));
    } catch (error) {
      if (key.current === selectedPath && !pending.signal.aborted)
        setMoreError((error as Error).message);
    } finally {
      if (key.current === selectedPath && !pending.signal.aborted)
        setLoadingMore(false);
    }
  }
  return { ...resource, rows, cursor, loadingMore, moreError, loadMore };
}
