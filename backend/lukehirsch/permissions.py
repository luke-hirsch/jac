"""Object-level permissions for JAC viewsets.

Per-viewset `get_queryset` scoping already keeps users from seeing each
other's rows. These permission classes are defense-in-depth: they catch any
code path that bypasses `get_queryset` (e.g. `Model.objects.get(pk=...)`
inside a custom action).
"""

from rest_framework import permissions


class IsOwner(permissions.BasePermission):
    """Object must be owned by the request user."""

    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id


class IsOwnerOrReadOnly(permissions.BasePermission):
    """Reads allowed for any authenticated user; writes require ownership.

    Used by `DomainViewSet` so system-default rows are visible to everyone
    but only editable by their owner (the sentinel user).
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.user_id == request.user.id
