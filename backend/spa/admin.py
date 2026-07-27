from django.contrib import admin

from spa.models import PersonalityQuestion, UserProfile


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


@admin.register(PersonalityQuestion)
class PersonalityQuestionAdmin(admin.ModelAdmin):
    list_display = ["slug", "prompt", "user", "order"]
    list_filter = ["user"]
    search_fields = ["slug", "prompt"]
    raw_id_fields = ["user"]
    ordering = ["user", "order"]


@admin.register(PortfolioBlock)
class PortfolioBlockAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "user", "favourite", "order", "is_active"]
    list_filter = ["kind", "favourite", "is_active"]
    search_fields = ["title", "body"]
    raw_id_fields = ["user"]
    filter_horizontal = ["domains"]


@admin.register(PortfolioLink)
class PortfolioLinkAdmin(admin.ModelAdmin):
    list_display = ["slug", "kind", "user", "application", "revoked_at", "created_at"]
    list_filter = ["kind"]
    search_fields = ["slug", "title"]
    raw_id_fields = ["user", "application"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(PortfolioVisit)
class PortfolioVisitAdmin(admin.ModelAdmin):
    list_display = ["link", "day", "count"]
    ordering = ["-day"]
    raw_id_fields = ["link"]
