export type Route = {
  section: string;
  parts: string[];
  params: URLSearchParams;
  valid: boolean;
};

export function parseRoute(hash: string): Route {
  try {
    const [pathname, search = ""] = hash.replace(/^#/, "").split("?");
    const parts = (pathname || "/reports")
      .split("/")
      .filter(Boolean)
      .map(decodeURIComponent);
    if (
      parts.some(
        (part) => part.includes("/") || part === ".." || part.includes("\\"),
      )
    )
      return {
        section: "missing",
        parts: [],
        params: new URLSearchParams(),
        valid: false,
      };
    return {
      section: parts[0] || "reports",
      parts: parts.slice(1),
      params: new URLSearchParams(search),
      valid: true,
    };
  } catch {
    return {
      section: "missing",
      parts: [],
      params: new URLSearchParams(),
      valid: false,
    };
  }
}
export function href(section: string, ...parts: string[]) {
  return "#/" + [section, ...parts].map(encodeURIComponent).join("/");
}
export function navigate(path: string) {
  window.location.hash = path.startsWith("#")
    ? path
    : "#" + (path.startsWith("/") ? path : "/" + path);
}
