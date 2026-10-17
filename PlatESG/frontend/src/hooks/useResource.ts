import { useEffect, useState } from "react";
import { request } from "../api";

export function useResource<T>(path: string | null): {
  data?: T;
  error?: string;
  loading: boolean;
  reload: () => void;
} {
  const [state, setState] = useState<{
    key: string | null;
    data?: T;
    error?: string;
    loading: boolean;
  }>({ key: null, loading: true });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!path) {
      setState({ key: path, loading: false });
      return;
    }
    const controller = new AbortController();
    setState({ key: path, loading: true });
    request<T>(path, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted)
          setState({ key: path, data, loading: false });
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted)
          setState({ key: path, error: error.message, loading: false });
      });
    return () => controller.abort();
  }, [path, revision]);
  return {
    data: state.key === path ? state.data : undefined,
    error: state.key === path ? state.error : undefined,
    loading: state.key === path ? state.loading : !!path,
    reload: () => setRevision((value) => value + 1),
  };
}
