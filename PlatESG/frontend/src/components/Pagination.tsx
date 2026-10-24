import { useLocation } from "../navigation";
import { Icon } from "./Ui";

export function usePage(total: number, size: number, param = "page") {
  const [params, patch] = useLocation();
  const pages = Math.max(1, Math.ceil(total / size));
  const requested = Number(params.get(param) || 1);
  const page = Math.min(
    pages,
    Math.max(1, Number.isFinite(requested) ? Math.floor(requested) : 1),
  );
  const start = (page - 1) * size;
  return {
    page,
    pages,
    start,
    end: Math.min(total, start + size),
    setPage: (value: number) =>
      patch({ [param]: value <= 1 ? null : String(value) }),
  };
}

function pageNumbers(page: number, pages: number): (number | "gap")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, index) => index + 1);
  const values = new Set([1, pages, page - 1, page, page + 1]);
  const sorted = [...values]
    .filter((value) => value >= 1 && value <= pages)
    .sort((a, b) => a - b);
  const output: (number | "gap")[] = [];
  for (const value of sorted) {
    const previous = output[output.length - 1];
    if (typeof previous === "number" && value - previous > 1)
      output.push("gap");
    output.push(value);
  }
  return output;
}

export function Pagination({
  page,
  pages,
  onChange,
  label = "分页",
}: {
  page: number;
  pages: number;
  onChange: (value: number) => void;
  label?: string;
}) {
  if (pages <= 1) return null;
  return (
    <nav className="pagination" aria-label={label}>
      <button
        type="button"
        className="page-step"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
        aria-label="上一页"
      >
        <Icon name="chevronLeft" size={16} />
        上一页
      </button>
      <div className="page-numbers">
        {pageNumbers(page, pages).map((value, index) =>
          value === "gap" ? (
            <span className="page-gap" key={"gap-" + index} aria-hidden="true">
              …
            </span>
          ) : (
            <button
              type="button"
              key={value}
              className={"page-number" + (value === page ? " active" : "")}
              aria-label={"第 " + value + " 页"}
              aria-current={value === page ? "page" : undefined}
              onClick={() => onChange(value)}
            >
              {value}
            </button>
          ),
        )}
      </div>
      <button
        type="button"
        className="page-step"
        disabled={page >= pages}
        onClick={() => onChange(page + 1)}
        aria-label="下一页"
      >
        下一页
        <Icon name="chevronRight" size={16} />
      </button>
    </nav>
  );
}
