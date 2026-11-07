"""Pushover MESSAGE provider."""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from obs.adapters.message.providers.base import MessageSendResult
from obs.adapters.message.providers.pushover_attachment import PushoverAttachmentError, PushoverImage, fetch_pushover_image

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_PRIORITIES = (-2, -1, 0, 1, 2)
PUSHOVER_EMERGENCY_PRIORITY = 2
PUSHOVER_MIN_RETRY_SECONDS = 30
PUSHOVER_MAX_EXPIRE_SECONDS = 10800


class PushoverConfig(BaseModel):
    enabled: bool = False
    api_token: str = Field(default="", json_schema_extra={"format": "password"})
    targets: dict[str, PushoverTarget] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_enabled_provider(self) -> PushoverConfig:
        if self.enabled and not self.api_token.strip():
            raise ValueError("Pushover api_token is required when provider is enabled")
        return self


class PushoverTarget(BaseModel):
    user_key: str = Field(default="", json_schema_extra={"format": "password"})
    device: str | None = None
    sound: str | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> PushoverTarget:
        if not self.user_key.strip():
            raise ValueError("Pushover user_key is required")
        return self


class PushoverMessageOptions(BaseModel):
    """Per-message Pushover options shared by MESSAGE bindings and the notification block.

    Unset options are ``None`` and never reach the Pushover API. Text options may
    still contain binding placeholders while a binding is configured; the
    provider validates them again after rendering.
    """

    # Errors must never echo the input: image_url may carry credentials.
    model_config = ConfigDict(hide_input_in_errors=True)

    priority: int | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    url: str | None = Field(default=None, max_length=512)
    url_title: str | None = Field(default=None, max_length=100)
    sound: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    format: Literal["text", "html", "monospace"] = "text"
    ttl: int | None = Field(default=None, gt=0)
    timestamp: int | None = Field(default=None, gt=0)
    retry: int | None = Field(default=None, ge=PUSHOVER_MIN_RETRY_SECONDS)
    expire: int | None = Field(default=None, gt=0, le=PUSHOVER_MAX_EXPIRE_SECONDS)

    @field_validator("image_url", "url", "url_title", "sound", mode="before")
    @classmethod
    def _blank_text_is_unset(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("priority", "ttl", "timestamp", "retry", "expire", mode="before")
    @classmethod
    def _blank_number_is_unset(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator("format", mode="before")
    @classmethod
    def _blank_format_is_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip().lower()
        return "text" if value in (None, "") else value


def _check_combinations(options: PushoverMessageOptions) -> None:
    """Cross-field rules, applied only where the options are actually used.

    They live outside the model so that stored options of a binding without a
    Pushover target (or one that only archives) never block saving it.
    """
    if options.priority is not None and options.priority not in PUSHOVER_PRIORITIES:
        raise ValueError("Pushover priority must be one of -2, -1, 0, 1 or 2")
    if options.priority == PUSHOVER_EMERGENCY_PRIORITY and (options.retry is None or options.expire is None):
        raise ValueError("Pushover emergency priority (2) requires retry and expire")


def check_static_url_title(raw: Any) -> None:
    """A configured URL title without any URL is a configuration error."""
    if not isinstance(raw, dict):
        return
    if str(raw.get("url_title") or "").strip() and not str(raw.get("url") or "").strip():
        raise ValueError("Pushover url_title requires a url")


def parse_pushover_options(raw: Any, *, priority: int | None = None) -> PushoverMessageOptions:
    """Validate *raw* options; *priority* is the fallback for a legacy top-level priority.

    Raises ``ValueError`` with a secret-free message: Pydantic's own string form
    echoes the rejected input, which may be an image URL with credentials.
    """
    data = dict(raw) if isinstance(raw, dict) else {}
    if not str(data.get("url") or "").strip():
        # A URL template that rendered empty leaves the message without a link
        # instead of failing it; the title alone would be meaningless. Static
        # configuration is checked separately (check_static_url_title).
        data.pop("url_title", None)
    if str(data.get("priority") if data.get("priority") is not None else "").strip() == "" and priority is not None:
        data["priority"] = priority
    try:
        emergency = int(data.get("priority")) == PUSHOVER_EMERGENCY_PRIORITY
    except (TypeError, ValueError):
        emergency = False
    if not emergency:
        # retry/expire only exist for emergency priority; a leftover value is
        # ignored by Pushover, so it must neither be sent nor fail validation.
        data.pop("retry", None)
        data.pop("expire", None)
    try:
        options = PushoverMessageOptions(**data)
    except ValidationError as exc:
        raise ValueError(validation_error_message(exc)) from None
    _check_combinations(options)
    return options


def validation_error_message(exc: ValidationError) -> str:
    """Join validation messages without the rejected input values."""
    messages = []
    for error in exc.errors(include_input=False, include_url=False):
        message = str(error.get("msg", "")).removeprefix("Value error, ")
        field = ".".join(str(part) for part in error.get("loc", ()))
        messages.append(f"{field}: {message}" if field else message)
    return "; ".join(messages) or "invalid Pushover options"


def build_pushover_payload(
    cfg: PushoverConfig,
    target: PushoverTarget,
    *,
    title: str | None,
    message: str,
    options: PushoverMessageOptions,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "token": cfg.api_token,
        "user": target.user_key,
        "message": message,
    }
    if title:
        payload["title"] = title
    if target.device:
        payload["device"] = target.device
    sound = options.sound or target.sound
    if sound:
        payload["sound"] = sound
    if options.priority is not None:
        payload["priority"] = options.priority
        if options.priority == PUSHOVER_EMERGENCY_PRIORITY:
            payload["retry"] = options.retry
            payload["expire"] = options.expire
    if options.url:
        payload["url"] = options.url
    if options.url_title:
        payload["url_title"] = options.url_title
    if options.format == "html":
        payload["html"] = 1
    elif options.format == "monospace":
        payload["monospace"] = 1
    if options.ttl is not None and options.priority != PUSHOVER_EMERGENCY_PRIORITY:
        payload["ttl"] = options.ttl
    if options.timestamp is not None:
        payload["timestamp"] = options.timestamp
    return payload


class PushoverProvider:
    provider_type = "pushover"
    config_schema = PushoverConfig
    target_schema = PushoverTarget

    async def send(
        self,
        *,
        provider_config: dict[str, Any],
        target_name: str,
        target_config: dict[str, Any],
        title: str | None,
        message: str,
        context: dict[str, Any],
    ) -> MessageSendResult:
        cfg = PushoverConfig(**provider_config)
        target = PushoverTarget(**target_config)
        try:
            options = parse_pushover_options(context.get("pushover"), priority=context.get("priority"))
        except ValueError as exc:
            return MessageSendResult("pushover", target_name, False, f"invalid Pushover options: {exc}")
        payload = build_pushover_payload(cfg, target, title=title, message=message, options=options)

        files = None
        if options.image_url:
            try:
                image = await _load_image(options.image_url, context.get("attachment_cache"))
            except PushoverAttachmentError as exc:
                return MessageSendResult("pushover", target_name, False, f"image attachment failed ({exc.code}): {exc.message}")
            files = {"attachment": (image.filename, image.content, image.content_type)}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if files is None:
                    response = await client.post(PUSHOVER_API_URL, data=payload)
                else:
                    response = await client.post(PUSHOVER_API_URL, data=payload, files=files)
        except httpx.TimeoutException:
            return MessageSendResult("pushover", target_name, False, "Pushover request timed out")
        except httpx.HTTPError as exc:
            return MessageSendResult("pushover", target_name, False, f"Pushover request failed ({type(exc).__name__})")
        if response.status_code >= 400:
            _ok, errors = _pushover_response_ok(response)
            detail = f"HTTP {response.status_code}"
            return MessageSendResult("pushover", target_name, False, f"{detail}: {errors}" if errors else detail)
        ok, detail = _pushover_response_ok(response)
        if not ok:
            return MessageSendResult("pushover", target_name, False, detail)
        return MessageSendResult("pushover", target_name, True)


async def _load_image(url: str, cache: Any) -> PushoverImage:
    """Fetch *url* once per message: every Pushover target gets the same frame.

    The adapter passes one ``attachment_cache`` dict per send; a failure is
    cached too, so a broken source is not retried for each further target.
    """
    if not isinstance(cache, dict):
        return await fetch_pushover_image(url)
    if url not in cache:
        try:
            cache[url] = await fetch_pushover_image(url)
        except PushoverAttachmentError as exc:
            cache[url] = exc
    cached = cache[url]
    if isinstance(cached, PushoverAttachmentError):
        raise cached
    return cached


def _pushover_response_ok(response: httpx.Response) -> tuple[bool, str]:
    try:
        body = response.json()
    except ValueError:
        return True, ""

    if not isinstance(body, dict) or "status" not in body:
        return True, ""

    status = body["status"]
    if status is True or status == 1 or (isinstance(status, str) and status.strip().lower() in {"1", "true"}):
        return True, ""

    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        return False, "; ".join(str(error) for error in errors)
    return False, f"pushover status={status}"
