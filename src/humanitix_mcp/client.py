"""Humanitix API async HTTP client (Phase 2)."""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator

import httpx

DEFAULT_BASE_URL = "https://api.humanitix.com"
REQUEST_TIMEOUT = httpx.Timeout(30.0)
DEFAULT_MAX_ITEMS = 500


class HumanitixError(Exception):
    """Base exception for Humanitix client errors.

    The response context never includes the API key.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ConfigurationError(HumanitixError):
    """Raised when the client is missing required configuration."""


class AuthenticationError(HumanitixError):
    """Raised when Humanitix rejects the API key (HTTP 401)."""


class AuthorizationError(HumanitixError):
    """Raised when the API key lacks permission for the resource (HTTP 403)."""


class NotFoundError(HumanitixError):
    """Raised when the requested resource does not exist (HTTP 404)."""


class ValidationError(HumanitixError):
    """Raised when the request payload or parameters are invalid (HTTP 422)."""


class BadRequestError(HumanitixError):
    """Raised when the request is malformed (HTTP 400)."""


class ServerError(HumanitixError):
    """Raised when Humanitix returns an internal server error (HTTP 500)."""


class TransportError(HumanitixError):
    """Raised when a network or timeout failure occurs."""


def _redact_url(url: str, key: str) -> str:
    """Return the URL with the API key removed, if present as a query param."""
    if not key:
        return url
    return url.replace(key, "<redacted>")


class HumanitixClient:
    """Async, read-only Humanitix Public API client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        max_retries: int = 3,
        page_throttle_seconds: float = 0.25,
    ) -> None:
        """Initialize the client.

        Args:
            api_key: Humanitix API key. Reads ``HUMANITIX_API_KEY`` from the
                environment if not provided.
            base_url: API base URL. Defaults to ``HUMANITIX_BASE_URL`` or
                ``https://api.humanitix.com``.
            max_retries: Maximum retry attempts for retryable failures.
            page_throttle_seconds: Delay between paginated requests. This is a
                conservative client-side throttle pending published Humanitix
                rate limits.
        """
        resolved_key = api_key or os.environ.get("HUMANITIX_API_KEY")
        if not resolved_key:
            raise ConfigurationError(
                "HUMANITIX_API_KEY is required. Set it as an environment variable "
                "or pass it to HumanitixClient(api_key=...).",
            )

        self._api_key = resolved_key
        self._base_url = (base_url or os.environ.get("HUMANITIX_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._max_retries = max_retries
        self._page_throttle_seconds = page_throttle_seconds

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "x-api-key": self._api_key,
                "Accept": "application/json",
            },
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HumanitixClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    def _map_http_error(self, response: httpx.Response) -> HumanitixError:
        """Map an HTTP error response to a typed exception."""
        status = response.status_code
        url = _redact_url(str(response.url), self._api_key)
        try:
            body = response.json()
        except Exception:
            body = response.text

        if status == 400:
            return BadRequestError(
                f"Bad request to {url}: {body}",
                status_code=status,
            )
        if status == 401:
            return AuthenticationError(
                f"Humanitix rejected the API key (401) for {url}. "
                "Check that HUMANITIX_API_KEY is set correctly.",
                status_code=status,
            )
        if status == 403:
            return AuthorizationError(
                f"The API key is not authorised to access {url}.",
                status_code=status,
            )
        if status == 404:
            return NotFoundError(
                f"Resource not found at {url}.",
                status_code=status,
            )
        if status == 422:
            return ValidationError(
                f"Invalid request parameters for {url}: {body}",
                status_code=status,
            )
        if status >= 500:
            return ServerError(
                f"Humanitix server error ({status}) at {url}: {body}",
                status_code=status,
            )
        return HumanitixError(
            f"Unexpected HTTP error ({status}) at {url}: {body}",
            status_code=status,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request with retries and consistent error handling."""
        attempt = 0
        last_error: Exception | None = None

        while attempt < self._max_retries:
            try:
                response = await self._client.request(method, path, params=params, **kwargs)
            except httpx.TimeoutException as exc:
                last_error = TransportError(
                    f"Request to {path} timed out after {REQUEST_TIMEOUT.read} seconds.",
                    status_code=None,
                )
                attempt += 1
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                continue
            except httpx.NetworkError as exc:
                last_error = TransportError(
                    f"Network error reaching {self._base_url}{path}: {exc}",
                    status_code=None,
                )
                attempt += 1
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                continue
            except httpx.HTTPError as exc:
                # Any other httpx error that is not timeout/network is surfaced
                # without retry (e.g., redirect loops).
                raise TransportError(
                    f"Transport failure for {path}: {exc}",
                    status_code=None,
                ) from exc

            if response.is_success:
                return response

            if response.status_code in (400, 401, 403, 404, 422):
                raise self._map_http_error(response)

            if response.status_code >= 500:
                last_error = self._map_http_error(response)
                attempt += 1
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                continue

            raise self._map_http_error(response)

        assert last_error is not None
        raise last_error

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a GET request and return the JSON response."""
        response = await self._request("GET", path, params=params)
        return response.json()

    async def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
        max_items: int = DEFAULT_MAX_ITEMS,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield items from a page-numbered endpoint.

        Requests pages until the API ``total`` is reached or ``max_items`` is
        met. A small delay is inserted between requests as a conservative
        client-side throttle pending published Humanitix rate limits.
        """
        if page_size < 1 or page_size > 100:
            raise ValidationError("page_size must be between 1 and 100.")
        if max_items < 1:
            raise ValidationError("max_items must be at least 1.")

        current_params = dict(params or {})
        current_params.setdefault("pageSize", page_size)
        page = 1
        yielded = 0

        while yielded < max_items:
            current_params["page"] = page
            data = await self.get(path, params=current_params)

            items = data.get("items", [])
            total = data.get("total", len(items))

            for item in items:
                if yielded >= max_items:
                    return
                yield item
                yielded += 1

            if yielded >= total or not items:
                return

            page += 1
            if self._page_throttle_seconds > 0:
                await asyncio.sleep(self._page_throttle_seconds)
