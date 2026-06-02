"""Cross-app DRF serializer mixins.

Lives in the project package (not in `jac/`) because both `jac` and
`llm_connector` need to scope writable related-field querysets to the request
user — without it, `PrimaryKeyRelatedField(queryset=Model.objects.all())` lets
user A reference user B's PKs on POST/PATCH.
"""

from typing import TYPE_CHECKING

from rest_framework import serializers
from rest_framework.relations import RelatedField

_SerializerBase = serializers.Serializer if TYPE_CHECKING else object


class ScopeRelatedToUserMixin(_SerializerBase):
    """Restrict named related-field querysets to rows owned by the request user.

    Subclasses set `user_scoped_fields` to a tuple of field names whose
    related model has a `user` FK that should match the request user.

    Works for both single (`PrimaryKeyRelatedField`) and `many=True` (wrapped
    in `ManyRelatedField`, where the queryset lives on `child_relation`).
    """

    user_scoped_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return
        for name in self.user_scoped_fields:
            self._scope_field_queryset(name, request)

    def _scope_field_queryset(self, name: str, request) -> None:
        field = self.fields.get(name)
        if field is None:
            return
        related: RelatedField = getattr(field, "child_relation", field)
        if hasattr(related, "queryset") and related.queryset is not None:
            related.queryset = related.queryset.filter(user=request.user)
