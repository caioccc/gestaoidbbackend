from django.contrib import admin

from .models import (
    CalendarEvent,
    FinancialEntry,
    FinancialExit,
    MonthlyClosing,
    Tither,
    TitheRecord,
)


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ['title', 'church', 'category', 'repeat_monthly', 'date', 'day', 'created_at']
    list_filter = ['category', 'repeat_monthly', 'church']
    search_fields = ['title', 'church__name']


@admin.register(FinancialEntry)
class FinancialEntryAdmin(admin.ModelAdmin):
    list_display = [
        'date', 'church', 'service_description', 'category', 'amount',
        'created_at',
    ]
    list_filter = ['category', 'date', 'church']
    search_fields = ['service_description', 'church__name']
    date_hierarchy = 'date'
    list_per_page = 50


@admin.register(FinancialExit)
class FinancialExitAdmin(admin.ModelAdmin):
    list_display = [
        'date', 'church', 'description', 'category', 'amount',
        'has_receipt', 'created_at',
    ]
    list_filter = ['category', 'date', 'church']
    search_fields = ['description', 'church__name']
    date_hierarchy = 'date'
    list_per_page = 50

    @admin.display(boolean=True, description='Comprovante')
    def has_receipt(self, obj):
        return bool(obj.receipt)


@admin.register(Tither)
class TitherAdmin(admin.ModelAdmin):
    list_display = ['name', 'church', 'is_anonymous', 'created_at']
    list_filter = ['is_anonymous', 'church']
    search_fields = ['name', 'church__name']


@admin.register(TitheRecord)
class TitheRecordAdmin(admin.ModelAdmin):
    list_display = ['tither', 'year', 'month', 'amount']
    list_filter = ['year', 'month', 'tither__church']
    search_fields = ['tither__name']
    list_per_page = 50


@admin.register(MonthlyClosing)
class MonthlyClosingAdmin(admin.ModelAdmin):
    list_display = [
        'church', 'year', 'month', 'is_closed', 'previous_balance',
        'total_entries', 'total_exits', 'final_balance',
    ]
    list_filter = ['is_closed', 'year', 'month', 'church']
    search_fields = ['church__name']
