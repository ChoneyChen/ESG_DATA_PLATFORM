from __future__ import annotations


class ModelProviderError(RuntimeError):
    """Provider-neutral model transport error consumed by the review kernel."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self._retryable = retryable

    @property
    def retryable(self) -> bool:
        return self._retryable

    @property
    def account_wide_rate_limit(self) -> bool:
        return False

    @property
    def minute_rate_limit(self) -> bool:
        return False

    @property
    def retry_after_seconds(self) -> float | None:
        return None
