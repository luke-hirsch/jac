"""Validate user-supplied LLM endpoint URLs to blunt SSRF.

`custom` / `ollama` configs let a user set an arbitrary `url` the server will POST to. Without
this, that's a server-side-request-forgery primitive against internal services (the server's own
localhost, the Redis port, cloud metadata at 169.254.169.254, …). We allow only http(s), and by
default only hosts that resolve to public addresses.

Self-hosting Ollama over a VPN is a first-class use case (the zero-cost thesis), so private /
loopback destinations are reachable when the *operator* vouches for them:

* ``settings.LLM_URL_ALLOWLIST`` — hostnames and/or CIDRs the operator trusts. Matched by exact
  (case-insensitive) hostname against the url host, or as a network against each resolved IP.
  Operator-controlled, so the allow path is immune to DNS rebinding — a user can't add entries.
* ``settings.LLM_URL_ALLOW_PRIVATE`` — blanket "trust my whole private net" for pure self-host.

Neither setting lifts the **hard block** — cloud-metadata / link-local, multicast, unspecified —
so even an allowlisted deployment can't be pivoted into the metadata endpoint.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError


def _in_allowlist(host: str, addr: ipaddress._BaseAddress) -> bool:
    """True if the operator vouched for this destination via ``LLM_URL_ALLOWLIST``.

    Each entry is tried as a CIDR/IP network against ``addr``, falling back to an exact
    (case-insensitive) hostname match against ``host``.
    """
    for entry in getattr(settings, "LLM_URL_ALLOWLIST", []):
        entry = entry.strip()
        if not entry:
            continue
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            if host and host.lower() == entry.lower():
                return True
        else:
            if addr.version == net.version and addr in net:
                return True
    return False


def _is_blocked_ip(ip: str, host: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> treat as unsafe
    # Hard block — no setting lifts these. Link-local carries the cloud-metadata endpoint.
    if addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return True
    # Public addresses are always fine.
    if not (addr.is_private or addr.is_loopback or addr.is_reserved):
        return False
    # Private / loopback / reserved: reachable only when the operator vouched for it.
    if _in_allowlist(host, addr):
        return False
    return not getattr(settings, "LLM_URL_ALLOW_PRIVATE", False)


def validate_safe_llm_url(url: str) -> None:
    """Raise ValidationError unless `url` is http(s) to an allowed host.

    Resolves the hostname and rejects if ANY resolved address is in a blocked range (so a name
    that maps to 127.0.0.1 or a metadata IP is caught). Private / loopback destinations pass only
    when the operator allowlists them (see module docstring). Empty url is allowed — callers only
    pass url-bearing providers here.
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
        if _is_blocked_ip(str(ip), host):
            raise ValidationError(
                f"LLM url resolves to a non-public address ({ip}); refusing to store it. "
                "Add its host or subnet to LLM_URL_ALLOWLIST to permit it."
            )
