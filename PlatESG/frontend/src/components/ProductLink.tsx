import type { ReactNode } from "react";
import { navigate, pageHref } from "../navigation";
import type { View } from "../types";
export function ProductLink({
  view,
  values = {},
  children,
  className,
}: {
  view: View;
  values?: Record<string, string>;
  children: ReactNode;
  className?: string;
}) {
  return (
    <a
      className={className}
      href={pageHref(view, values)}
      onClick={(event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)
          return;
        event.preventDefault();
        navigate(view, values);
      }}
    >
      {children}
    </a>
  );
}
