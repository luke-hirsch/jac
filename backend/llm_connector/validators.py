"""Validate user-supplied LLM endpoint URLs to blunt SSRF.

`custom` / `ollama` configs let a user set an arbitrary `url` the server will POST to. Without
this, that's a server-side-request-forgery primitive against internal services (localhost, the
Redis port, cloud metadata at 169.254.169.254, …). We allow only http(s) to hosts that do NOT
resolve into private / loopback / link-local / reserved ranges.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from django.core.exceptions import ValidationError


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> treat as unsafe
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_safe_llm_url(url: str) -> None:
    """Raise ValidationError unless `url` is http(s) to a public host.

    Resolves the hostname and rejects if ANY resolved address is in a blocked range (so a name
    that maps to 127.0.0.1 or a metadata IP is caught). Empty url is allowed — callers only pass
    url-bearing providers here.
    """
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("LLM url must use http or https.")
    host = parsed.hostname
    if not host:
        raise ValidationError("LLM url must include a host.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValidationError(f"LLM url host does not resolve: {host}") from exc
    for info in infos:
        ip = info[4][0]
        if _is_blocked_ip(str(ip)):
            raise ValidationError(
                f"LLM url resolves to a non-public address ({ip}); refusing to store it."
            )
