"""Typed module-boundary exceptions with sanitized public diagnostics."""

from invoice_agent.models import ErrorCode, ErrorInfo, redact


class AgentError(Exception):
    def __init__(
        self, code: ErrorCode, message: str, *, retryable: bool = False, fatal: bool = False
    ):
        self.info = ErrorInfo(code=code, message=redact(message), retryable=retryable, fatal=fatal)
        super().__init__(self.info.message)

    @property
    def code(self) -> ErrorCode:
        return self.info.code


class InvoiceAgentError(AgentError):
    def __init__(self, info: ErrorInfo):
        super().__init__(info.code, info.message, retryable=info.retryable, fatal=info.fatal)
