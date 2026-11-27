from django.contrib import admin

from .models import (
    Event,
    EventCheckin,
    EventCheckinAttempt,
    EventCheckinConfig,
    EventCheckinTeamMember,
    EventRegistration,
    EventSession,
)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'church', 'source', 'date', 'is_active',
        'registration_token_is_active', 'updated_at',
    )
    list_filter = ('source', 'is_active', 'church')
    search_fields = ('title', 'church__name')
    readonly_fields = ('registration_token',)


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = (
        'display_name', 'event', 'kind', 'source', 'status', 'code', 'registered_at',
    )
    list_filter = ('kind', 'source', 'status', 'church')
    search_fields = ('name', 'code', 'member__name', 'event__title')
    readonly_fields = ('code',)


@admin.register(EventCheckinConfig)
class EventCheckinConfigAdmin(admin.ModelAdmin):
    list_display = ('event_title', 'church', 'mode', 'is_active', 'updated_at')
    list_filter = ('mode', 'is_active', 'church')
    search_fields = ('event__title', 'checkin_event__title', 'church__name')


@admin.register(EventSession)
class EventSessionAdmin(admin.ModelAdmin):
    list_display = (
        'event_title', 'occurrence_date', 'church', 'token_is_active',
        'opens_at', 'closes_at',
    )
    list_filter = ('token_is_active', 'church')
    search_fields = ('event__title', 'checkin_event__title', 'token')
    readonly_fields = ('token',)


@admin.register(EventCheckinTeamMember)
class EventCheckinTeamMemberAdmin(admin.ModelAdmin):
    list_display = ('config', 'member', 'role', 'church')
    list_filter = ('role', 'church')
    search_fields = ('member__name', 'config__event__title', 'config__checkin_event__title')


@admin.register(EventCheckin)
class EventCheckinAdmin(admin.ModelAdmin):
    list_display = (
        'display_name', 'session', 'source', 'status', 'in_roster',
        'operator', 'checked_at',
    )
    list_filter = ('source', 'status', 'in_roster', 'church')
    search_fields = (
        'member__name', 'member__card_number', 'registration__name',
        'registration__code', 'session__event__title',
        'session__checkin_event__title',
    )


@admin.register(EventCheckinAttempt)
class EventCheckinAttemptAdmin(admin.ModelAdmin):
    list_display = ('result', 'session', 'member', 'operator', 'created_at')
    list_filter = ('result', 'church')
    search_fields = ('member__name', 'detail')
