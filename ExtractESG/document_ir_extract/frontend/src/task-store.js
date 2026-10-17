import {taskRevision} from "./status.js";
const resources = new Map();
const listeners = new Map();

export function publishTask(key, value) {
  const previous = resources.get(key);
  if (previous && taskRevision(previous) === taskRevision(value)) return false;
  resources.set(key, value);
  for (const callback of listeners.get(key) || []) callback(value);
  return true;
}
export const readTask = (key) => resources.get(key);
export function observeTask(key, callback) {
  if (!listeners.has(key)) listeners.set(key, new Set());
  listeners.get(key).add(callback);
  if (resources.has(key)) callback(resources.get(key));
  return () => {
    listeners.get(key)?.delete(callback);
    if (!listeners.get(key)?.size) listeners.delete(key);
  };
}
