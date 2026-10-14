"""Secure image download for Pushover attachments.

Pushover only accepts an attachment as a multipart upload, so OBS fetches the
image itself. Every hop of that download — the original URL and each redirect —
passes the same policy:

- public targets only over HTTPS;
- internal targets (HTTP or HTTPS) only when their resolved IP is released in
  the operator-managed URL Target Allowlist;
- the connection is pinned to the IP that was checked (no DNS rebinding) and
  the peer address is verified after connecting;
- content type, declared size, streamed size and the leading magic bytes are
  checked; fixed timeouts bound every request and the download as a whole.

Error messages name the host at most — never URL credentials, path or query.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import ssl
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

import httpx

from obs.security.url_targets import UrlTargetBlockedError, build_pinned_url_targets, is_non_public_address

PUSHOVER_ATTACHMENT_MAX_BYTES = 5_242_880  # Pushover limit: 5 MB per message
MAX_REDIRECTS = 3
DOWNLOAD_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
DOWNLOAD_DEADLINE_SECONDS = 20.0

ALLOWED_IMAGE_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}
_CONTENT_TYPE_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAGIC_PREFIX_BYTES = 12
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class PushoverAttachmentError(Exception):
    """An image could not be loaded safely; ``code`` is stable, ``message`` is secret-free."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PushoverImage:
    filename: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class _PinnedRequest:
    host: str
    candidates: list[tuple[str, str]]  # (pinned URL without userinfo, pinned IP)
    headers: dict[str, str]
    extensions: dict[str, str]


def _host_for_message(url: str) -> str:
    try:
        return urlparse(url).hostname or "?"
    except ValueError:
        return "?"


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, (parsed.hostname or "").lower(), port


def _userinfo_auth(url: str) -> _CameraAuth | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None  # _plan_request rejects the URL itself
    if parsed.username is None:
        return None
    return _CameraAuth(unquote(parsed.username), unquote(parsed.password or ""))


_CHALLENGE_SCHEME = re.compile(r"(?:^|,)\s*([A-Za-z][A-Za-z0-9!#$%&\'*+.^_`|~-]*)(?=\s+[^\s=,]|\s*(?:,|$))")


def _challenges(headers: list[str]) -> list[tuple[str, str]]:
    """Split WWW-Authenticate into ``(scheme, challenge)`` pairs.

    A challenge's parameters are ``name=value`` pairs; a scheme is a token that is
    not followed by ``=`` (RFC 9110 section 11.6.1). Quoted strings are masked
    first, so realm text such as ``"Digest-Cam"`` is never taken for a scheme.
    """
    result: list[tuple[str, str]] = []
    for header in headers:
        masked = re.sub(r'"(?:[^"\\]|\\.)*"', lambda match: "x" * len(match.group(0)), header)
        starts = [(match.start(1), match.group(1).lower()) for match in _CHALLENGE_SCHEME.finditer(masked)]
        for index, (start, scheme) in enumerate(starts):
            end = starts[index + 1][0] if index + 1 < len(starts) else len(header)
            result.append((scheme, header[start:end].strip().rstrip(",").strip()))
    return result


class _CameraAuth(httpx.Auth):
    """Answer the camera's challenge with Digest or Basic — never preemptively.

    The first request carries no credentials, so a Digest camera never sees the
    password in clear; Basic is only sent when the camera asks for Basic.
    """

    def __init__(self, username: str, password: str) -> None:
        self._basic = httpx.BasicAuth(username, password)
        self._digest = httpx.DigestAuth(username, password)

    def auth_flow(self, request: httpx.Request):
        response = yield request
        if response.status_code != 401:
            return
        challenges = _challenges(response.headers.get_list("www-authenticate"))
        digest_challenge = next((text for scheme, text in challenges if scheme == "digest"), None)
        if digest_challenge is None:
            if any(scheme == "basic" for scheme, _text in challenges):
                yield next(self._basic.auth_flow(request))
            return
        # httpx's DigestAuth only reads a header line of its own ("Digest …"),
        # so hand it exactly the Digest challenge — also when the camera combined
        # it with Basic in one line — instead of falling back to Basic.
        response = httpx.Response(401, headers={"www-authenticate": digest_challenge}, request=request)
        digest = self._digest.auth_flow(request)
        next(digest)
        while True:
            try:
                next_request = digest.send(response)
            except StopIteration:
                return
            response = yield next_request


def _plan_request(url: str) -> _PinnedRequest:
    """Validate *url* against the attachment policy and pin it to its checked IPs."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        _ = parsed.port  # raises for a malformed port
    except ValueError as exc:
        raise PushoverAttachmentError("invalid_url", "image_url is not a valid URL") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not hostname:
        raise PushoverAttachmentError("invalid_url", "image_url must be an http:// or https:// URL with a host")

    try:
        pinned_urls, headers, extensions = build_pinned_url_targets(url, preserve_userinfo=False)
    except UrlTargetBlockedError as exc:
        decision = exc.decision
        if decision.blocked_ips:
            suggestion = decision.suggested_target or decision.blocked_ips[0]
            raise PushoverAttachmentError(
                "not_allowlisted",
                f"image host {decision.host or hostname} resolves to the internal address {decision.blocked_ips[0]}, "
                f"which is not released in the URL Target Allowlist; add {suggestion} there to allow this image source",
            ) from None
        if decision.host and not decision.resolved_ips:
            raise PushoverAttachmentError("dns_error", f"image host {decision.host} could not be resolved") from None
        raise PushoverAttachmentError("invalid_url", "image_url is not a valid URL") from None
    except UnicodeError:
        raise PushoverAttachmentError("invalid_url", "image_url is not a valid URL") from None
    except ValueError:  # the target was allowed but left no usable address
        raise PushoverAttachmentError("dns_error", f"image host {hostname} did not resolve to a usable address") from None

    # build_pinned_url_targets pins one URL per checked IP, in resolution order.
    candidates = [(pinned_url, urlparse(pinned_url).hostname or "") for pinned_url in pinned_urls]
    if scheme == "http":
        # Plain HTTP only to internal (allowlisted) addresses. A dual-stack LAN
        # camera may also publish a global IPv6 address — skip it, never use it.
        candidates = [(pinned_url, ip) for pinned_url, ip in candidates if is_non_public_address(ip)]
    if not candidates:
        raise PushoverAttachmentError(
            "insecure_http",
            f"image host {hostname} is a public target; external image sources must use HTTPS",
        )
    # An uncompressed body keeps the size limit meaningful: a compressed one
    # would be inflated in memory before any byte count could stop it.
    return _PinnedRequest(hostname, candidates, {**headers, "Accept-Encoding": "identity"}, extensions)


def _peer_matches(response: httpx.Response, pinned_ip: str) -> bool:
    stream = response.extensions.get("network_stream")
    if stream is None:
        return True
    server_addr = stream.get_extra_info("server_addr")
    if not server_addr:
        return True
    try:
        return ipaddress.ip_address(server_addr[0]) == ipaddress.ip_address(pinned_ip)
    except ValueError:
        return False


async def _send_with_auth(
    client: httpx.AsyncClient, request: httpx.Request, auth: _CameraAuth | None, plan: _PinnedRequest, pinned_ip: str
) -> httpx.Response:
    """Run the auth flow without httpx's own loop, which reads every challenge body in full.

    A 401 challenge is closed unread, so an oversized or endless body from the
    camera cannot bypass the attachment size limit.
    """
    flow = auth.auth_flow(request) if auth is not None else iter((request,))
    next_request = next(flow)
    while True:
        response = await client.send(next_request, stream=True, follow_redirects=False)
        # Verify every peer before reacting to it, so credentials only ever
        # answer a challenge from the checked IP.
        if not _peer_matches(response, pinned_ip):
            await response.aclose()
            raise PushoverAttachmentError("ip_mismatch", f"image host {plan.host} answered from an unexpected IP address")
        if auth is None:
            return response
        try:
            next_request = flow.send(response)
        except StopIteration:
            return response
        await response.aclose()


async def _open(client: httpx.AsyncClient, plan: _PinnedRequest, auth: _CameraAuth | None) -> httpx.Response:
    tls_failed = False
    for pinned_url, pinned_ip in plan.candidates:
        request = client.build_request("GET", pinned_url, headers=plan.headers, extensions=plan.extensions)
        try:
            response = await _send_with_auth(client, request, auth, plan, pinned_ip)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            tls_failed = tls_failed or _is_tls_failure(exc)
            continue  # an unreachable or broken address (often IPv6) must not hide a working one
        return response
    if tls_failed:
        raise PushoverAttachmentError(
            "tls_error",
            f"the TLS certificate of image host {plan.host} could not be verified; use a trusted certificate, "
            "or plain HTTP for an internal camera released in the URL Target Allowlist",
        )
    raise PushoverAttachmentError("connect_error", f"could not connect to image host {plan.host}")


def _is_tls_failure(exc: BaseException) -> bool:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return True
        cause = cause.__cause__ or cause.__context__
    return False


def _content_type(response: httpx.Response) -> str:
    raw = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    content_type = _CONTENT_TYPE_ALIASES.get(raw, raw)
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise PushoverAttachmentError(
            "invalid_content_type",
            f"image source returned content type '{raw or 'none'}'; allowed are {', '.join(ALLOWED_IMAGE_TYPES)}",
        )
    return content_type


def _check_declared_length(response: httpx.Response) -> None:
    raw = response.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > PUSHOVER_ATTACHMENT_MAX_BYTES:
        raise PushoverAttachmentError("too_large", "image is larger than the Pushover limit of 5 MB")


def _magic_matches(head: bytes, content_type: str) -> bool:
    if content_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


async def _read_image(response: httpx.Response, content_type: str) -> bytes:
    body = bytearray()
    magic_checked = False
    async for chunk in response.aiter_bytes():  # compressed bodies were rejected before
        body.extend(chunk)
        if len(body) > PUSHOVER_ATTACHMENT_MAX_BYTES:
            raise PushoverAttachmentError("too_large", "image is larger than the Pushover limit of 5 MB")
        if not magic_checked and len(body) >= _MAGIC_PREFIX_BYTES:
            magic_checked = True
            if not _magic_matches(bytes(body[:_MAGIC_PREFIX_BYTES]), content_type):
                raise PushoverAttachmentError("invalid_content_type", f"image content does not match the declared type {content_type}")
    if not body:
        raise PushoverAttachmentError("invalid_content_type", "image source returned an empty body")
    if not magic_checked and not _magic_matches(bytes(body), content_type):
        raise PushoverAttachmentError("invalid_content_type", f"image content does not match the declared type {content_type}")
    return bytes(body)


def _filename(url: str, content_type: str) -> str:
    extension = ALLOWED_IMAGE_TYPES[content_type]
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1]).rsplit(".", 1)[0]
    stem = _FILENAME_UNSAFE.sub("_", name).strip("._")[:60]
    return f"{stem or 'image'}.{extension}"


async def _download(url: str) -> PushoverImage:
    original_origin = _origin(url)
    original_auth = _userinfo_auth(url)
    current = url
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=False, trust_env=False) as client:
        for hop in range(MAX_REDIRECTS + 1):
            try:
                plan = await asyncio.to_thread(_plan_request, current)
            except PushoverAttachmentError as exc:
                if hop == 0:
                    raise
                raise PushoverAttachmentError("redirect_blocked", f"image redirect rejected: {exc.message}") from exc
            auth = _userinfo_auth(current) or (original_auth if _origin(current) == original_origin else None)
            response = await _open(client, plan, auth)
            try:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise PushoverAttachmentError("http_error", f"image host {plan.host} sent a redirect without a location")
                    current = urljoin(current, location)
                    continue
                if not 200 <= response.status_code < 300:
                    raise PushoverAttachmentError("http_error", f"image host {plan.host} answered with HTTP {response.status_code}")
                content_type = _content_type(response)
                _check_declared_length(response)
                if response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
                    raise PushoverAttachmentError("invalid_content_type", "image source sent a compressed body, which is not supported")
                content = await _read_image(response, content_type)
                return PushoverImage(_filename(current, content_type), content, content_type)
            finally:
                await response.aclose()
    raise PushoverAttachmentError("redirect_blocked", f"image source exceeded {MAX_REDIRECTS} redirects")


async def fetch_pushover_image(url: str) -> PushoverImage:
    """Download *url* as a Pushover attachment or raise :class:`PushoverAttachmentError`."""
    try:
        async with asyncio.timeout(DOWNLOAD_DEADLINE_SECONDS):
            return await _download(url)
    except PushoverAttachmentError:
        raise
    except httpx.InvalidURL:
        raise PushoverAttachmentError("invalid_url", "image_url is not a valid URL") from None
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise PushoverAttachmentError("timeout", f"image download from {_host_for_message(url)} timed out") from exc
    except httpx.HTTPError as exc:
        raise PushoverAttachmentError(
            "connect_error",
            f"image download from {_host_for_message(url)} failed ({type(exc).__name__})",
        ) from exc
