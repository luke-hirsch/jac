"""spa.blocks — what each portfolio block kind holds, and the rules for it.

One module for the rules the block serializer, the model's `clean()` and the public payload
share: which fields a kind needs, what its `data` may hold, which files it takes, and what an
uploaded audio or video file really is. Kinds are plain strings here (`PortfolioBlock.Kind`'s
values), so nothing in this module imports the models.
"""

from pathlib import Path

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import EmailValidator, URLValidator
from rest_framework import serializers

# ── the kind table ────────────────────────────────────────────────────────────

# The one field outside `data` a kind can't do without, and what to say when it's missing.
_NEEDS = {
    "text": ("body", "A text block needs a body."),
    "image": ("image", "An image block needs an image."),
    "quote": ("body", "A quote block needs the quote (body)."),
    "media": ("media", "A media block needs an audio or video file."),
}

# The files a kind takes. For a media block, `image` is the poster.
FILE_FIELDS = {"image": ("image",), "media": ("media", "image")}

FIGURES_MIN, FIGURES_MAX = 2, 4
LINK_ITEMS_MIN, LINK_ITEMS_MAX = 1, 12
MAX_URL_LEN = 500


def missing_fields(kind: str, *, body, image, media) -> dict[str, str]:
    """The kind's required field outside `data`, if it's empty, as {field: message}."""
    need = _NEEDS.get(kind)
    if need is None:
        return {}
    field, message = need
    value = {"body": (body or "").strip(), "image": image, "media": media}[field]
    return {} if value else {field: message}


def takes_file(kind: str, field: str) -> bool:
    return field in FILE_FIELDS.get(kind, ())


# ── data ──────────────────────────────────────────────────────────────────────


def _invalid(message: str) -> serializers.ValidationError:
    return serializers.ValidationError({"data": [message]})


def _text(data: dict, key: str, name: str, longest: int, required=True) -> str:
    """One string from `data`: stripped, length-checked. `name` is how the message calls it."""
    value = data.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _invalid(f"{name} must be text.")
    value = value.strip()
    # Postgres can't store a NUL, and multipart data never meets the JSON parser that refuses it.
    if "\x00" in value:
        raise _invalid("NUL characters aren't allowed.")
    if required and not value:
        raise _invalid(f"{name} is required.")
    if len(value) > longest:
        raise _invalid(f"{name} is at most {longest} characters.")
    return value


def _rows(data: dict, key: str, shortest: int, longest: int) -> list[dict]:
    rows = data.get(key)
    if (
        not isinstance(rows, list)
        or not shortest <= len(rows) <= longest
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise _invalid(f"data.{key} must be a list of {shortest} to {longest} rows.")
    return rows


def _url(value: str, name: str, schemes: tuple[str, ...]) -> str:
    """Only the schemes a kind allows: the public page puts this in an href, so a `javascript:`
    link must never get that far."""
    scheme, _, rest = value.partition(":")
    scheme = scheme.lower()
    try:
        if scheme == "mailto" and "mailto" in schemes:
            EmailValidator()(rest.partition("?")[0])  # the address, without ?subject=…
        elif scheme in schemes:
            URLValidator(schemes=list(schemes))(value)
        else:
            raise DjangoValidationError("scheme")
    except DjangoValidationError:
        allowed = " or ".join(
            "mailto:" if s == "mailto" else f"{s}://" for s in schemes
        )
        raise _invalid(f"{name}: only {allowed} links.") from None
    return value


def _quote(data: dict) -> dict:
    return {
        "by": _text(data, "by", "data.by", 100),
        "role": _text(data, "role", "data.role", 150, required=False),
    }


def _figures(data: dict) -> dict:
    rows = _rows(data, "figures", FIGURES_MIN, FIGURES_MAX)
    return {
        "figures": [
            {
                "value": _text(row, "value", f"data.figures[{i}].value", 12),
                "label": _text(row, "label", f"data.figures[{i}].label", 60),
            }
            for i, row in enumerate(rows)
        ]
    }


def _cta(data: dict) -> dict:
    return {
        "label": _text(data, "label", "data.label", 40),
        "url": _url(
            _text(data, "url", "data.url", MAX_URL_LEN), "data.url", ("https", "mailto")
        ),
    }


def _linklist(data: dict) -> dict:
    rows = _rows(data, "items", LINK_ITEMS_MIN, LINK_ITEMS_MAX)
    items = []
    for i, row in enumerate(rows):
        name = f"data.items[{i}]"
        items.append(
            {
                "title": _text(row, "title", f"{name}.title", 120),
                "url": _url(
                    _text(row, "url", f"{name}.url", MAX_URL_LEN),
                    f"{name}.url",
                    ("https", "http"),
                ),
                "note": _text(row, "note", f"{name}.note", 200, required=False),
            }
        )
    return {"items": items}


_CLEANERS = {"quote": _quote, "figures": _figures, "cta": _cta, "linklist": _linklist}


def clean_block_data(kind: str, data) -> dict:
    """A kind's `data`, checked and normalised: only the kind's own keys, every one of them
    present, strings stripped. Kinds without fields of their own get `{}`. Raises the
    serializer's ValidationError, shaped `{"data": [message]}`."""
    if data is None or data == "":
        data = {}
    if not isinstance(data, dict):
        raise _invalid("data must be an object.")
    cleaner = _CLEANERS.get(kind)
    return cleaner(data) if cleaner else {}


def public_block_data(kind: str, data) -> dict:
    """What the public page gets of a block's `data`: the same rules again, so a row written past
    the serializer (the shell, a bug) shows nothing rather than a key or a link it shouldn't."""
    try:
        return clean_block_data(kind, data)
    except serializers.ValidationError:
        return {}


def block_text(block) -> str:
    """A block as the embed ranking reads it: title, body and the words in its `data`."""
    data = public_block_data(block.kind, block.data)
    parts = [block.title, block.body]
    if block.kind == "quote":
        parts.append(" ".join(v for v in (data.get("by"), data.get("role")) if v))
    for row in data.get("figures", []):
        parts.append(f"{row['value']} {row['label']}")
    if block.kind == "cta":
        parts.append(data.get("label", ""))
    for row in data.get("items", []):
        parts.append(" ".join(v for v in (row["title"], row["note"]) if v))
    return "\n".join(p.strip() for p in parts if p and p.strip())


# ── audio and video ───────────────────────────────────────────────────────────

# The extension a stored media file carries is the one its bytes proved, and nginx answers with
# the type that extension names (deploy/image/media.inc).
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}
MEDIA_HEAD_BYTES = 64

# ISO base media files all start with an `ftyp` box; its major brand says what's inside. HEIC
# and AVIF images and QuickTime films share the format, and their brands aren't here.
_M4A_BRANDS = {b"M4A ", b"M4B "}
_MP4_BRANDS = {
    b"isom",
    b"iso2",
    b"iso4",
    b"iso5",
    b"iso6",
    b"mp41",
    b"mp42",
    b"avc1",
    b"dash",
    b"M4V ",
    b"MSNV",
}
# An Ogg file's first packet names its codec: audio only (no Theora video).
_OGG_AUDIO = (b"\x01vorbis", b"OpusHead", b"\x7fFLAC")


def sniff_media(head: bytes) -> str | None:
    """The extension a file's first bytes prove (`.mp4 .webm .mp3 .m4a .ogg`), or None. Never the
    client's file name or content type."""
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _M4A_BRANDS:
            return ".m4a"
        if brand in _MP4_BRANDS:
            return ".mp4"
        return None
    # EBML: Matroska, and WebM is the part of it browsers play.
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm" if b"webm" in head else None
    if head[:3] == b"ID3":
        return ".mp3"
    # A bare MPEG audio frame: 11 sync bits, then layer III. AAC's ADTS frames say layer 0.
    if len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return ".mp3" if ((head[1] >> 1) & 0x03) == 0x01 else None
    if head[:4] == b"OggS" and len(head) > 27:
        start = 27 + head[26]  # past the page header and its segment table
        return ".ogg" if head[start:].startswith(_OGG_AUDIO) else None
    return None


def media_type(field_file) -> str | None:
    """The content type a stored media file is served with, from its extension."""
    if not field_file:
        return None
    return MEDIA_TYPES.get(Path(field_file.name).suffix.lower())
