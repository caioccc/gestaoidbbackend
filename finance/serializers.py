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
            'description', 'start_time', 'end_time', 'members', 'members_names',
            'repeat_monthly', 'repeat_weekly', 'weekdays', 'repeat_interval',
            'repeat_end_date', 'date', 'month', 'day', 'created_at',
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
        instance = self.instance

        start_time = attrs.get('start_time')
        end_time = attrs.get('end_time')
        existing_start = getattr(instance, 'start_time', None) if instance else None
        effective_start = start_time if start_time is not None else existing_start
        if end_time and effective_start and end_time <= effective_start:
            raise serializers.ValidationError(
                {'end_time': 'O horário de fim deve ser após o início.'}
            )

        recurrence_keys = (
            'date', 'month', 'day', 'repeat_monthly', 'repeat_weekly',
            'weekdays', 'repeat_interval', 'repeat_end_date',
        )
        if not any(k in attrs for k in recurrence_keys):
            return attrs

        def eff(key, default=None):
            if key in attrs:
                return attrs[key]
            return getattr(instance, key, default) if instance is not None else default

        repeat_monthly = eff('repeat_monthly', False) is True
        repeat_weekly = eff('repeat_weekly', False) is True
        weekdays = eff('weekdays', []) or []
        interval = eff('repeat_interval', 1)
        anchor = eff('date', None)
        repeat_end_date = eff('repeat_end_date', None)
        day = eff('day', None)

        if repeat_monthly and repeat_weekly:
            raise serializers.ValidationError(
                {'detail': 'Escolha apenas um tipo de recorrência.'}
            )

        if repeat_weekly:
            if not weekdays:
                raise serializers.ValidationError(
                    {'weekdays': 'Informe ao menos um dia da semana.'}
                )
            if any(not isinstance(d, int) or not 0 <= d <= 6 for d in weekdays):
                raise serializers.ValidationError(
                    {'weekdays': 'Dias inválidos (use 0=Seg .. 6=Dom).'}
                )
            if interval > 2:
                raise serializers.ValidationError(
                    {'repeat_interval': 'Intervalo máximo de 2 semanas.'}
                )
            if interval > 1 and anchor is None:
                raise serializers.ValidationError(
                    {'date': 'Evento quinzenal precisa da data âncora (1ª ocorrência).'}
                )
            if anchor is not None and anchor.isoweekday() - 1 not in weekdays:
                raise serializers.ValidationError(
                    {'date': 'A data âncora deve cair em um dos dias da semana escolhidos.'}
                )
            if repeat_end_date and anchor and repeat_end_date < anchor:
                raise serializers.ValidationError(
                    {'repeat_end_date': 'O fim da recorrência precede a âncora.'}
                )
            attrs['repeat_monthly'] = False
            attrs['month'] = None
            attrs['day'] = None
        elif repeat_monthly:
            if day is None:
                raise serializers.ValidationError(
                    {'day': 'Informe o dia para um evento recorrente mensal.'}
                )
            attrs['repeat_weekly'] = False
            attrs['weekdays'] = []
            attrs['repeat_interval'] = 1
            attrs['repeat_end_date'] = None
            attrs['date'] = None
        else:
            if anchor is None:
                raise serializers.ValidationError(
                    {'date': 'Informe a data para um evento pontual.'}
                )
            attrs['repeat_weekly'] = False
            attrs['weekdays'] = []
            attrs['repeat_interval'] = 1
            attrs['repeat_end_date'] = None
            attrs['month'] = None
            attrs['day'] = None
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
            'description', 'start_time', 'end_time',
            'repeat_monthly', 'repeat_weekly', 'weekdays', 'repeat_interval',
            'repeat_end_date', 'date', 'month', 'day',
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
