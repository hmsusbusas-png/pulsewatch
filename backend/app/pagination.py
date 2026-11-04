from collections.abc import Callable, Sequence
from typing import Any

from fastapi import Request, Response

PAGINATION_HEADERS: dict[str, Any] = {
    "Link": {
        "description": 'Ссылка на следующую страницу в формате RFC 8288: `<...>; rel="next"`',
        "schema": {"type": "string"},
    },
    "X-Next-Cursor": {
        "description": "Курсор следующей страницы; отсутствует на последней",
        "schema": {"type": "string"},
    },
}


def paginate[Item](
    request: Request,
    response: Response,
    items: Sequence[Item],
    limit: int,
    cursor_of: Callable[[Item], object],
) -> Sequence[Item]:
    if len(items) < limit or not items:
        return items
    last = items[-1]
    cursor = cursor_of(last)
    url = request.url.include_query_params(cursor=cursor, limit=limit)
    target = f"{url.path}?{url.query}" if url.query else url.path
    response.headers["Link"] = f'<{target}>; rel="next"'
    response.headers["X-Next-Cursor"] = str(cursor)
    return items
