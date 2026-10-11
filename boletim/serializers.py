"""Serializers do app boletim (Folder / Boletim)."""
from django.conf import settings
from rest_framework import serializers

from .models import FolderCulto, FolderCultoRow
from .services import default_qr_urls


class FolderCultoRowSerializer(serializers.ModelSerializer):
    """Linha do agenda de cultos do folder."""

    service_type_display = serializers.CharField(
        source='get_service_type_display', read_only=True,
    )

    class Meta:
        model = FolderCultoRow
        fields = [
            'id', 'service_type', 'service_type_display', 'date',
            'start_time', 'end_time', 'title', 'preacher', 'sort_order',
        ]


class FolderCultoSerializer(serializers.ModelSerializer):
    """Folder com linhas de agenda, QR, aniversariantes e link público."""

    tipo_display = serializers.CharField(source='get_tipo_display', read_only=True)
    cover_template_display = serializers.CharField(
        source='get_cover_template_display', read_only=True,
    )
    service_type_display = serializers.CharField(
        source='get_service_type_display', read_only=True, default='',
    )
    rows = FolderCultoRowSerializer(many=True, required=False)
    created_by_name = serializers.CharField(
        source='created_by.name', read_only=True, default='',
    )
    updated_by_name = serializers.CharField(
        source='updated_by.name', read_only=True, default='',
    )
    public_url = serializers.SerializerMethodField()
    default_qr = serializers.SerializerMethodField()

    class Meta:
        model = FolderCulto
        fields = [
            'id', 'church', 'tipo', 'tipo_display', 'title', 'subtitle',
            'service_type', 'service_type_display',
            'cover_template', 'cover_template_display', 'theme_color',
            'background_color', 'cover_font',
            'mensagem_pastoral', 'article_title', 'versiculo',
            'reference_start', 'reference_end',
            'programacao', 'anuncios', 'aniversariantes', 'qr', 'social',
            'blocks', 'block_order', 'rows',
            'public_hash', 'public_url', 'default_qr',
            'created_by', 'created_by_name', 'updated_by',
            'updated_by_name', 'created_at', 'updated_at',
        ]
        read_only_fields = ['church', 'created_by', 'updated_by', 'public_hash']

    def get_public_url(self, obj):
        if not obj.public_hash:
            return ''
        return f'{settings.FRONTEND_URL}/folder/{obj.public_hash}'

    def get_default_qr(self, obj):
        return default_qr_urls(obj.church)

    def validate(self, attrs):
        start = attrs.get('reference_start') or getattr(self.instance, 'reference_start', None)
        end = attrs.get('reference_end') or getattr(self.instance, 'reference_end', None)
        if start and end and end < start:
            raise serializers.ValidationError({
                'reference_end': 'O fim do período deve ser igual ou posterior ao início.',
            })

        request = self.context.get('request')
        church = request.user.church if request and request.user else None
        tipo = attrs.get('tipo') or getattr(self.instance, 'tipo', None)
        if church is not None and start is not None and tipo:
            qs = FolderCulto.objects.filter(
                church=church, tipo=tipo, reference_start=start,
            )
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({
                    'reference_start': (
                        f'Já existe um folder {tipo} para este período.'
                    ),
                })
        return attrs

    def _set_rows(self, folder, rows_data):
        allowed = {
            'service_type', 'date', 'start_time', 'end_time',
            'title', 'preacher', 'sort_order',
        }
        for index, row in enumerate(rows_data):
            payload = {k: v for k, v in row.items() if k in allowed and v is not None}
            payload.setdefault('sort_order', index)
            folder.rows.create(**payload)

    def create(self, validated_data):
        rows_data = validated_data.pop('rows', [])
        if not validated_data.get('qr'):
            validated_data.pop('qr', None)
            validated_data['qr'] = default_qr_urls(
                self.context['request'].user.church
            )
        folder = FolderCulto.objects.create(**validated_data)
        self._set_rows(folder, rows_data)
        return folder

    def update(self, instance, validated_data):
        rows_data = validated_data.pop('rows', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if rows_data is not None:
            instance.rows.all().delete()
            self._set_rows(instance, rows_data)
        return instance