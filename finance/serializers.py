from rest_framework import serializers

from .models import (
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
    receipt_url = serializers.SerializerMethodField()

    class Meta:
        model = FinancialExit
        fields = [
            'id', 'church', 'date', 'description', 'category',
            'category_display', 'amount', 'receipt', 'receipt_url',
            'created_at',
        ]
        read_only_fields = ['church', 'created_at']

    def get_receipt_url(self, obj):
        if obj.receipt:
            request = self.context.get('request')
            if request is not None:
                return request.build_absolute_uri(obj.receipt.url)
            return obj.receipt.url
        return None

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
