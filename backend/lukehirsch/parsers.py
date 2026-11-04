"""Request parsing shared by every API view."""

from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class ObjectJSONParser(JSONParser):
    """No endpoint here takes a JSON list or a bare value. Refused at the door, so `request.data`
    is a mapping in every view and `.get()` can't be a 500."""

    def parse(self, stream, media_type=None, parser_context=None):
        data = super().parse(stream, media_type, parser_context)
        if not isinstance(data, dict):
            raise ParseError("Expected a JSON object.")
        return data
