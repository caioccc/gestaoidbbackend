from django.contrib import admin

from .models import (
    Band,
    BandSetlist,
    BandSetlistItem,
    Ministry,
    MinistryRole,
    RosterAssignment,
    SetlistItem,
    Song,
    VolunteerRoster,
    WorshipSetlist,
)


@admin.register(Band)
class BandAdmin(admin.ModelAdmin):
    list_display = ('name', 'church', 'color', 'leader', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


class BandSetlistItemInline(admin.TabularInline):
    model = BandSetlistItem
    extra = 0
    raw_id_fields = ('song',)


@admin.register(BandSetlist)
class BandSetlistAdmin(admin.ModelAdmin):
    list_display = ('date', 'description', 'band', 'theme', 'created_by')
    list_filter = ('band',)
    search_fields = ('description', 'theme')
    inlines = [BandSetlistItemInline]


class MinistryRoleInline(admin.TabularInline):
    model = MinistryRole
    extra = 1


@admin.register(Ministry)
class MinistryAdmin(admin.ModelAdmin):
    list_display = ('name', 'church', 'color', 'leader', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)
    inlines = [MinistryRoleInline]


@admin.register(MinistryRole)
class MinistryRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'ministry', 'is_active')
    list_filter = ('ministry', 'is_active')


class RosterAssignmentInline(admin.TabularInline):
    model = RosterAssignment
    extra = 0
    raw_id_fields = ('user',)


@admin.register(VolunteerRoster)
class VolunteerRosterAdmin(admin.ModelAdmin):
    list_display = ('date', 'time', 'theme', 'is_published', 'created_by')
    list_filter = ('is_published',)
    search_fields = ('theme',)
    inlines = [RosterAssignmentInline]


@admin.register(RosterAssignment)
class RosterAssignmentAdmin(admin.ModelAdmin):
    list_display = ('roster', 'user', 'ministry', 'role', 'status')
    list_filter = ('status', 'ministry')


@admin.register(Song)
class SongAdmin(admin.ModelAdmin):
    list_display = ('title', 'artist', 'youtube_id', 'church_key', 'bpm', 'times_played')
    search_fields = ('title', 'artist', 'tags')
    list_filter = ('is_active',)


@admin.register(WorshipSetlist)
class WorshipSetlistAdmin(admin.ModelAdmin):
    list_display = ('roster', 'created_at')
    raw_id_fields = ('roster',)


@admin.register(SetlistItem)
class SetlistItemAdmin(admin.ModelAdmin):
    list_display = ('setlist', 'order', 'song', 'custom_key')
    raw_id_fields = ('setlist', 'song')