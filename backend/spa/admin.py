from django.contrib import admin

from spa.models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "display_name",
        "theme",
        "contrast",
        "timezone",
        "updated_at",
    ]
    list_filter = ["theme", "contrast", "email_reminders"]
    search_fields = ["user__email", "display_name", "bio"]
    raw_id_fields = ["user"]
    readonly_fields = ["updated_at"]
    fieldsets = [
        (
            None,
            {
                "fields": ["street", "address_line2", "zip", "city", "country"],
            },
        )
    ]
