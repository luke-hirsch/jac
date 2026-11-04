from django.contrib import admin

from .models import (
    Certification,
    Domain,
    Education,
    Job,
    JobPostAddress,
    JobPosting,
    Language,
    Location,
    Project,
    Skill,
)


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("city", "country", "zip")
    search_fields = ("city", "country")


@admin.register(Education)
class EducationAdmin(admin.ModelAdmin):
    list_display = ("institution", "field_of_study", "degree", "started", "ended")
    search_fields = ("institution", "field_of_study", "degree")
    filter_horizontal = ("skills", "domains")


@admin.register(Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = ("name", "issuer", "issued_on", "expires_on")
    search_fields = ("name", "issuer")
    filter_horizontal = ("skills", "domains")


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "proficiency", "years_of_experience_override")
    list_filter = ("category", "proficiency", "domains")
    search_fields = ("name",)
    filter_horizontal = ("domains", "related_skills", "builds_on")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "job_type", "started", "ended")
    list_filter = ("job_type", "domains")
    search_fields = ("title", "company")
    filter_horizontal = ("skills", "domains")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "started", "ended")
    list_filter = ("domains",)
    search_fields = ("name",)
    filter_horizontal = ("skills", "domains")
    autocomplete_fields = ("job",)


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ("name", "fluency")
    list_filter = ("fluency",)
    search_fields = ("name",)


class JobPostAddressInline(admin.StackedInline):
    model = JobPostAddress
    extra = 0


@admin.register(JobPosting)
class JobPostingAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "language", "created_at")
    list_filter = ("language",)
    search_fields = ("title", "posting_text")
    inlines = [JobPostAddressInline]
