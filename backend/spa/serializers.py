from rest_framework import serializers

from spa.models import UserProfile


class UserProfileSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = UserProfile
        fields = (
            "id",
            "user",
            "display_name",
            "avatar",
            "bio",
            "phone",
            "website",
            "linkedin_url",
            "github_url",
            "timezone",
            "theme",
            "contrast",
            "email_reminders",
            "updated_at",
            "street",
            "address_line2",
            "zip",
            "city",
            "country",
        )
        read_only_fields = ("id", "updated_at")
