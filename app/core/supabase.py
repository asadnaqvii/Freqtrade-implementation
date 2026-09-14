"""Thin PostgREST client.

Two ways to talk to the database, and the difference matters:

  as_user(jwt)  -- requests carry the caller's own token, so RLS decides what
                   they can see. Every user-facing read and write goes through
                   this. If a handler forgets an owner filter, the database
                   still refuses; the policy is the backstop, not the handler.

  service()     -- requests carry the service role key, which bypasses RLS.
                   Only the worker and the bot use it, for work that is not on
                   behalf of a signed-in user (claiming jobs, writing
                   heartbeats). Never reachable from a browser.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Sequence

import httpx

from app.core.config import ConfigError, get_settings

log = logging.getLogger(__name__)

# PostgREST answers 300 for an ambiguous embedded resource and 406 for a
# single-row request that matched none; both are worth surfacing verbatim.
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class SupabaseError(RuntimeError):
    """A PostgREST request failed."""

    def __init__(self, status: int, body: str, *, hint: str | None = None) -> None:
        self.status = status
        self.body = body
        self.hint = hint
        message = f"supabase returned {status}: {body}"
        if hint:
            message = f"{message} ({hint})"
        super().__init__(message)


class SupabaseClient:
    """A single-purpose PostgREST wrapper.

    Deliberately not a general ORM. It covers the handful of verbs this project
    needs and keeps the query construction explicit at the call site.
    """

    def __init__(self, token: str, *, is_service_role: bool = False) -> None:
        settings = get_settings()
        if not settings.supabase.url:
            raise ConfigError("SUPABASE_URL is not set")
        self._base = settings.supabase.rest_url
        self._apikey = settings.supabase.anon_key or token
        self._token = token
        self.is_service_role = is_service_role

    # -- construction ------------------------------------------------------
    @classmethod
    def service(cls) -> "SupabaseClient":
        settings = get_settings()
        return cls(settings.supabase.require_service_key(), is_service_role=True)

    @classmethod
    def as_user(cls, jwt: str) -> "SupabaseClient":
        return cls(jwt, is_service_role=False)

    # -- plumbing ----------------------------------------------------------
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._apikey,
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        headers = self._headers({"Prefer": prefer} if prefer else None)
        url = f"{self._base}/{table}"
        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.request(
                method, url, params=params, json=json_body, headers=headers
            )

        # 300 Multiple Choices, not just 4xx: PostgREST answers 300 when an
        # embedded resource is ambiguous, and its body is an error document. A
        # `>= 400` check lets that through as if it were data, and the caller
        # then indexes an error dict as though it were a row.
        if response.status_code >= 300:
            hint = None
            if response.status_code in (401, 403):
                hint = (
                    "the row exists but RLS refused it, or the token is wrong -- "
                    "check owner_id and which client you used"
                )
            elif response.status_code == 300:
                hint = (
                    "ambiguous embedded resource: two tables here are joined by more "
                    "than one foreign key, so the relationship has to be named "
                    "explicitly, e.g. other_table!fk_name(columns)"
                )
            raise SupabaseError(response.status_code, response.text, hint=hint)

        if not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return response.text

    # -- verbs -------------------------------------------------------------
    def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, str] | None = None,
        order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"select": columns}
        params.update(filters or {})
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        result = self._request("GET", table, params=params)
        if result is None:
            return []
        if isinstance(result, dict):
            # A select always returns a collection. Anything else means the
            # response was not what we asked for, and returning it would push a
            # confusing failure into the caller instead of here.
            raise SupabaseError(
                200, json.dumps(result)[:400],
                hint=f"expected a list of rows from {table}, got a single object",
            )
        return result

    def select_one(
        self, table: str, *, columns: str = "*", filters: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        rows = self.select(table, columns=columns, filters=filters, limit=1)
        return rows[0] if rows else None

    def insert(
        self, table: str, rows: dict[str, Any] | Sequence[dict[str, Any]], *, returning: bool = True
    ) -> list[dict[str, Any]]:
        prefer = "return=representation" if returning else "return=minimal"
        payload = rows if isinstance(rows, list) else [rows]
        result = self._request("POST", table, json_body=payload, prefer=prefer)
        return result or []

    def upsert(
        self,
        table: str,
        rows: dict[str, Any] | Sequence[dict[str, Any]],
        *,
        on_conflict: str,
        returning: bool = True,
    ) -> list[dict[str, Any]]:
        prefer = "resolution=merge-duplicates,"
        prefer += "return=representation" if returning else "return=minimal"
        payload = rows if isinstance(rows, list) else [rows]
        result = self._request(
            "POST", table, params={"on_conflict": on_conflict}, json_body=payload, prefer=prefer
        )
        return result or []

    def update(
        self, table: str, values: dict[str, Any], *, filters: dict[str, str]
    ) -> list[dict[str, Any]]:
        if not filters:
            # PostgREST would happily update the whole table. Refuse instead.
            raise ValueError("update requires filters; refusing an unfiltered write")
        result = self._request(
            "PATCH", table, params=filters, json_body=values, prefer="return=representation"
        )
        return result or []

    def delete(self, table: str, *, filters: dict[str, str]) -> list[dict[str, Any]]:
        if not filters:
            raise ValueError("delete requires filters; refusing an unfiltered delete")
        result = self._request(
            "DELETE", table, params=filters, prefer="return=representation"
        )
        return result or []

    def rpc(self, function: str, args: dict[str, Any] | None = None) -> Any:
        return self._request("POST", f"rpc/{function}", json_body=args or {})

    # -- convenience -------------------------------------------------------
    def insert_chunked(
        self, table: str, rows: Iterable[dict[str, Any]], *, chunk_size: int = 500
    ) -> int:
        """Insert many rows without building one enormous request body.

        Backtests routinely produce thousands of trades; a single POST of that
        size is a good way to meet a gateway timeout.
        """
        batch: list[dict[str, Any]] = []
        written = 0
        for row in rows:
            batch.append(row)
            if len(batch) >= chunk_size:
                self.insert(table, batch, returning=False)
                written += len(batch)
                batch = []
        if batch:
            self.insert(table, batch, returning=False)
            written += len(batch)
        return written


def service_client() -> SupabaseClient:
    return SupabaseClient.service()
