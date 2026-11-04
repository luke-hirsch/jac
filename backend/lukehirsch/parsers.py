"""Request parsing shared by every API view."""

import io

from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class ObjectJSONParser(JSONParser):
    """No endpoint here takes a JSON list or a bare value. Refused at the door, so `request.data`
    is a mapping in every view and `.get()` can't be a 500. The same goes for an escaped NUL:
    Postgres can't store it in text or jsonb, and JSONFields don't check. A raw NUL byte is already
    invalid JSON. A literal `\\\\u0000` in a text gets refused too, which is fine."""

    def parse(self, stream, media_type=None, parser_context=None):
        raw = stream.read()
        if b"\\u0000" in raw:
            raise ParseError("NUL characters aren't allowed.")
        data = super().parse(io.BytesIO(raw), media_type, parser_context)
        if not isinstance(data, dict):
            raise ParseError("Expected a JSON object.")
        return data
