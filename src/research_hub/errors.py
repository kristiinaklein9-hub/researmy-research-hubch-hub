"""v0.89.0 - structured exception hierarchy for agent-native error reasoning."""

from __future__ import annotations

from typing import Any


class ResearchHubError(Exception):
    """Base for all research-hub errors that agents can reason about."""

    error_code: str = "research_hub_error"

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        next_steps: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context = dict(context or {})
        self.next_steps = list(next_steps or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "context": self.context,
            "next_steps": self.next_steps,
        }


class MissingCredential(ResearchHubError):
    error_code = "missing_credential"

    def __init__(
        self,
        name: str,
        env_var: str,
        *,
        fallback_paths_tried: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        message = message or f"Missing credential: {name}. Set ${env_var}."
        super().__init__(
            message,
            context={
                "name": name,
                "env_var": env_var,
                "fallback_paths_tried": list(fallback_paths_tried or []),
            },
            next_steps=[f"export {env_var}=<value>"],
        )


class RequiresAuthRefresh(ResearchHubError):
    error_code = "requires_auth_refresh"

    def __init__(
        self,
        service: str,
        fix_command: str,
        *,
        message: str | None = None,
    ) -> None:
        message = message or f"{service} session expired. Run: {fix_command}"
        super().__init__(
            message,
            context={"service": service},
            next_steps=[fix_command],
        )


class MissingExternalTool(ResearchHubError):
    error_code = "missing_external_tool"

    def __init__(
        self,
        tool: str,
        install_hint: str,
        *,
        message: str | None = None,
    ) -> None:
        message = message or f"{tool!r} not on PATH. {install_hint}"
        super().__init__(
            message,
            context={"tool": tool},
            next_steps=[install_hint],
        )


class UpstreamRateLimited(ResearchHubError):
    error_code = "upstream_rate_limited"

    def __init__(
        self,
        service: str,
        *,
        retry_after: float | None = None,
        message: str | None = None,
    ) -> None:
        message = message or f"{service} rate-limited (HTTP 429)"
        super().__init__(
            message,
            context={"service": service, "retry_after": retry_after},
            next_steps=[],
        )


class UpstreamUnavailable(ResearchHubError):
    error_code = "upstream_unavailable"

    def __init__(
        self,
        service: str,
        status_code: int | None = None,
        *,
        message: str | None = None,
    ) -> None:
        message = message or f"{service} unreachable (status={status_code})"
        super().__init__(
            message,
            context={"service": service, "status_code": status_code},
            next_steps=[],
        )
