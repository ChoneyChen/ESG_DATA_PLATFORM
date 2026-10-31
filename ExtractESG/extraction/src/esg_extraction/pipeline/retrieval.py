"""Bounded object pagination. No terms, clause content or filesystem ownership."""


def retrieve_candidates(documents, ir_run_id, intent, maximum):
    if not callable(getattr(documents, "search_page", None)):
        raise ValueError(
            "DocumentPort requires ObjectSearchPage with total, cursor and query identity"
        )
    result, cursors, query = [], set(), None
    while True:
        page = documents.search_page(ir_run_id, intent)
        if query is not None and query != page.query_digest:
            raise ValueError("Search revision changed between object pages")
        query = page.query_digest
        result.extend(page.objects[: maximum - len(result)])
        if len(result) >= maximum or not page.next_cursor:
            return result, {
                "total": page.total,
                "returned": len(result),
                "query_digest": query,
                "truncated": len(result) < page.total,
                "scope": page.scope,
            }
        if not page.objects or page.next_cursor in cursors:
            raise ValueError("Object search did not advance its cursor")
        cursors.add(page.next_cursor)
        intent = intent.model_copy(update={"cursor": page.next_cursor})
