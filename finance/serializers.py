from rest_framework import serializers

from .models import (
    CalendarEvent,
    DepartmentCategory,
    FinancialEntry,
    FinancialExit,
    MonthlyClosing,
    Tither,
    TitheRecord,
)


class FinancialEntrySerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(
        source='get_category_display', read_only=True,
    )

    class Meta:
        model = FinancialEntry
        fields = [
            'id', 'church', 'date', 'service_description',
            'category', 'category_display', 'amount', 'created_at',
        ]
        read_only_fields = ['church', 'created_at']

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('O valor deve ser maior que 0.')
        return value


class FinancialExitSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(
        source='get_category_display', read_only=True,
    )

    class Meta:
        model = FinancialExit
        fields = [
            'id', 'church', 'date', 'description', 'category',
            'category_display', 'amount', 'created_at',
        ]
        read_only_fields = ['church', 'created_at']

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('O valor deve ser maior que 0.')
        return value


class TitherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tither
        fields = ['id', 'church', 'name', 'is_anonymous', 'created_at']
        read_only_fields = ['church', 'created_at']


class TitheRecordSerializer(serializers.ModelSerializer):
    tither_name = serializers.CharField(source='tither.name', read_only=True)

    class Meta:
        model = TitheRecord
        fields = ['id', 'tither', 'tither_name', 'year', 'month', 'amount']

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('O valor deve ser maior que 0.')
        return value


class MonthlyClosingSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyClosing
        fields = [
            'id', 'church', 'year', 'month', 'is_closed',
            'previous_balance', 'total_entries', 'total_exits',
            'final_balance',
        ]
        read_only_fields = [
            'church', 'previous_balance', 'total_entries',
            'total_exits', 'final_balance',
        ]


class CalendarEventSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(
        source='get_category_display', read_only=True,
    )
    audience_display = serializers.CharField(
        source='get_audience_display', read_only=True,
    )
    created_by_name = serializers.SerializerMethodField()
    members_names = serializers.SerializerMethodField()

    class Meta:
        model = CalendarEvent
        fields = [
            'id', 'church', 'audience', 'audience_display', 'created_by',
            'created_by_name', 'title', 'category', 'category_display',
            'description', 'start_time', 'members', 'members_names',
            'repeat_monthly', 'date', 'month', 'day', 'created_at',
        ]
        read_only_fields = ['church', 'created_by', 'created_at']

    def get_created_by_name(self, obj):
        return obj.created_by.name if obj.created_by else ''

    def get_members_names(self, obj):
        return [
            {'id': m.id, 'name': m.name}
            for m in obj.members.all().order_by('name')
        ]

    def validate(self, attrs):
        repeat_monthly = attrs.get('repeat_monthly')
        if repeat_monthly is False and attrs.get('date') is None:
            raise serializers.ValidationError(
                {'date': 'Informe a data para um evento pontual.'}
            )
        if repeat_monthly is True and attrs.get('day') is None:
            raise serializers.ValidationError(
                {'day': 'Informe o dia para um evento recorrente.'}
            )
        return attrs


class PublicCalendarEventSerializer(serializers.ModelSerializer):
    """Evento exposto na página pública (hash) — sem membros nem autores."""
    category_display = serializers.CharField(
        source='get_category_display', read_only=True,
    )

    class Meta:
        model = CalendarEvent
        fields = [
            'id', 'title', 'category', 'category_display',
            'description', 'start_time',
            'repeat_monthly', 'date', 'month', 'day',
        ]


class CategorySerializer(serializers.Serializer):
    """Helper para expor as categorias disponíveis."""
    value = serializers.CharField()
    label = serializers.CharField()

    @classmethod
    def many_categories(cls):
        return [
            {'value': c.value, 'label': c.label}
            for c in DepartmentCategory
        ]
