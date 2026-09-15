from rest_framework import serializers

from accounts import services as accounts_services

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


class BandPhotoField(serializers.Field):
    """Foto da banda: aceita data URL (base64) e devolve a URL do Cloudinary."""

    def to_internal_value(self, data):
        if data in (None, '', False):
            return None
        uploaded = accounts_services.data_url_to_file(data, 'band_photo.png')
        if uploaded is None:
            raise serializers.ValidationError('Foto inválida.')
        return uploaded

    def to_representation(self, value):
        return accounts_services.cloudinary_url(value)


class BandSerializer(serializers.ModelSerializer):
    leader_name = serializers.SerializerMethodField()
    song_count = serializers.SerializerMethodField()
    photo = BandPhotoField(required=False, allow_null=True)

    class Meta:
        model = Band
        fields = [
            'id', 'church', 'name', 'color', 'photo', 'leader', 'leader_name',
            'is_active', 'song_count', 'created_at',
        ]
        read_only_fields = ['church']

    def get_leader_name(self, obj):
        return obj.leader.name if obj.leader_id else ''

    def get_song_count(self, obj):
        count = getattr(obj, 'song_count', None)
        if count is None:
            count = obj.songs.count()
        return count


class MinistryRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MinistryRole
        fields = ['id', 'ministry', 'name', 'is_active']


class MinistrySerializer(serializers.ModelSerializer):
    roles = MinistryRoleSerializer(many=True, read_only=True)
    leader_name = serializers.SerializerMethodField()

    class Meta:
        model = Ministry
        fields = ['id', 'church', 'name', 'color', 'leader', 'leader_name', 'is_active', 'roles']
        read_only_fields = ['church']

    def get_leader_name(self, obj):
        return obj.leader.name if obj.leader_id else ''


class VolunteerSerializer(serializers.ModelSerializer):
    """Mini-serializer do usuário voluntário (sem dados sensíveis)."""

    class Meta:
        model = None  # atribuído dinamicamente abaixo

    @classmethod
    def build(cls):
        from django.contrib.auth import get_user_model

        User = get_user_model()

        class _VolunteerSerializer(serializers.ModelSerializer):
            display_name = serializers.SerializerMethodField()

            class Meta:
                model = User
                fields = ['id', 'name', 'email', 'display_name']

            def get_display_name(self, obj):
                return obj.name or obj.email

        return _VolunteerSerializer


VolunteerSerializer = VolunteerSerializer.build()


class RosterAssignmentSerializer(serializers.ModelSerializer):
    ministry_name = serializers.CharField(source='ministry.name', read_only=True)
    ministry_color = serializers.CharField(source='ministry.color', read_only=True)
    role_name = serializers.CharField(source='role.name', read_only=True)
    user_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = RosterAssignment
        fields = [
            'id', 'roster', 'ministry', 'ministry_name', 'ministry_color',
            'role', 'role_name', 'user', 'user_name', 'status', 'status_display',
            'notes',
        ]
        read_only_fields = ['user_name', 'status_display']

    def get_user_name(self, obj):
        return obj.user.name or obj.user.email

    def validate(self, attrs):
        roster = attrs.get('roster') or getattr(self.instance, 'roster', None)
        user = attrs.get('user') or getattr(self.instance, 'user', None)
        role = attrs.get('role') or getattr(self.instance, 'role', None)
        ministry = attrs.get('ministry') or getattr(self.instance, 'ministry', None)

        if (
            role is not None
            and ministry is not None
            and role.ministry_id != ministry.id
        ):
            raise serializers.ValidationError(
                {'role': 'A função selecionada não pertence a este ministério.'}
            )

        if roster is not None and user is not None:
            conflicting = RosterAssignment.objects.filter(
                roster=roster,
                user=user,
            ).exclude(pk=self.instance.pk if self.instance else None)
            conflicting_list = list(conflicting)
            if conflicting_list:
                raise serializers.ValidationError(
                    {
                        'user': (
                            f'{user.name or user.email} já está escalado '
                            f'como "{conflicting_list[0].role.name}" no mesmo culto '
                            f'({roster.date}). Para escalá-lo em outra função, '
                            'remova a atribuição anterior primeiro.'
                        )
                    }
                )
        return attrs


class SetlistItemSerializer(serializers.ModelSerializer):
    song_title = serializers.CharField(source='song.title', read_only=True)
    song_artist = serializers.CharField(source='song.artist', read_only=True)

    class Meta:
        model = SetlistItem
        fields = [
            'id', 'setlist', 'song', 'song_title', 'song_artist',
            'order', 'custom_key', 'notes',
        ]


class WorshipSetlistSerializer(serializers.ModelSerializer):
    items = SetlistItemSerializer(many=True, read_only=True)

    class Meta:
        model = WorshipSetlist
        fields = ['id', 'roster', 'items', 'created_at', 'updated_at']


class SongSerializer(serializers.ModelSerializer):
    band_name = serializers.CharField(source='band.name', read_only=True)
    band_color = serializers.CharField(source='band.color', read_only=True)

    class Meta:
        model = Song
        fields = [
            'id', 'church', 'band', 'band_name', 'band_color',
            'title', 'artist', 'youtube_id', 'youtube_title',
            'thumbnail_url', 'duration_seconds', 'original_key', 'church_key',
            'bpm', 'time_signature', 'chords', 'chords_json', 'lyrics',
            'tags', 'times_played', 'last_played', 'is_active', 'created_at',
        ]
        read_only_fields = ['church', 'times_played', 'last_played']

    def validate(self, attrs):
        title = (attrs.get('title') or '').strip()
        if not title:
            raise serializers.ValidationError({'title': 'Informe o título da música.'})
        attrs['title'] = title
        return attrs


class SongHistorySerializer(serializers.Serializer):
    """Histórico de cultos em que a música foi tocada."""

    date = serializers.DateField()
    roster_id = serializers.IntegerField()
    theme = serializers.CharField(required=False, allow_blank=True)
    custom_key = serializers.CharField(required=False, allow_blank=True)


class SongEnrichPayloadSerializer(serializers.Serializer):
    youtube_id = serializers.CharField()


class SetlistCreateSerializer(serializers.Serializer):
    """Payload para definir a ordem das músicas de um culto.

    `items` é uma lista de `{song: int, order: int, custom_key?, notes?}`
    (a ordem é derivada da posição quando `order` não é informado).
    """

    items = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False,
    )

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError('A setlist precisa de ao menos uma música.')
        cleaned = []
        for i, item in enumerate(items):
            song_id = item.get('song')
            if not song_id:
                raise serializers.ValidationError(f'Item {i + 1}: campo "song" obrigatório.')
            cleaned.append({
                'song': song_id,
                'order': int(item.get('order') or (i + 1)),
                'custom_key': (item.get('custom_key') or '').strip(),
                'notes': (item.get('notes') or '').strip(),
            })
        return cleaned


class RosterSerializer(serializers.ModelSerializer):
    assignments = RosterAssignmentSerializer(many=True, read_only=True)
    setlist = WorshipSetlistSerializer(read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = VolunteerRoster
        fields = [
            'id', 'church', 'date', 'time', 'theme', 'notes', 'is_published',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'assignments', 'setlist',
        ]
        read_only_fields = ['church', 'created_by']

    def get_created_by_name(self, obj):
        return obj.created_by.name if obj.created_by_id else ''

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            validated_data['created_by'] = request.user
        return super().create(validated_data)


class RosterCreatePayloadSerializer(RosterSerializer):
    """Aceita `assignments` na criação (voluntários por ministério/função)."""

    assignments = RosterAssignmentSerializer(many=True, required=False)

    def create(self, validated_data):
        request = self.context.get('request')
        assignments_data = validated_data.pop('assignments', [])
        roster = super().create(validated_data)
        errors = []
        for index, data in enumerate(assignments_data):
            try:
                serializer = RosterAssignmentSerializer(
                    data=data, context=self.context,
                )
                serializer.is_valid(raise_exception=True)
                serializer.save(roster=roster)
            except serializers.ValidationError as exc:
                errors.append({f'assignments[{index}]': exc.detail})
        if errors:
            # Arquivo de volta o roster para evitar estado parcial indesejado
            roster.delete()
            raise serializers.ValidationError(errors)
        return roster


class BandSetlistItemSerializer(serializers.ModelSerializer):
    song_title = serializers.CharField(source='song.title', read_only=True)
    song_artist = serializers.CharField(source='song.artist', read_only=True)
    song_church_key = serializers.CharField(source='song.church_key', read_only=True)
    song_bpm = serializers.IntegerField(source='song.bpm', read_only=True)

    class Meta:
        model = BandSetlistItem
        fields = [
            'id', 'setlist', 'song', 'song_title', 'song_artist',
            'song_church_key', 'song_bpm',
            'order', 'custom_key', 'notes',
        ]


class BandSetlistSerializer(serializers.ModelSerializer):
    items = BandSetlistItemSerializer(many=True, read_only=True)
    band_name = serializers.CharField(source='band.name', read_only=True)
    band_color = serializers.CharField(source='band.color', read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = BandSetlist
        fields = [
            'id', 'church', 'band', 'band_name', 'band_color', 'date',
            'description', 'theme', 'notes', 'created_by', 'created_by_name',
            'created_at', 'updated_at', 'items',
        ]
        read_only_fields = ['church', 'created_by']

    def get_created_by_name(self, obj):
        return obj.created_by.name if obj.created_by_id else ''


class BandSetlistPayloadSerializer(serializers.Serializer):
    """Payload de criar/atualizar setlist com `items` ({song, order?, custom_key?, notes?}).

    A ordem é derivada da posição quando `order` não é informado.
    """

    band = serializers.PrimaryKeyRelatedField(
        queryset=Band.objects.none(), required=False, allow_null=True,
    )
    date = serializers.DateField()
    description = serializers.CharField(max_length=200)
    theme = serializers.CharField(required=False, allow_blank=True, max_length=200)
    notes = serializers.CharField(required=False, allow_blank=True)
    items = serializers.ListField(
        child=serializers.DictField(), required=False, allow_empty=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        church = getattr(getattr(request, 'user', None), 'church', None)
        if church is not None:
            self.fields['band'].queryset = Band.objects.filter(church=church)

    def validate_items(self, items):
        cleaned = []
        for i, item in enumerate(items or []):
            song_id = item.get('song')
            if not song_id:
                raise serializers.ValidationError(f'Item {i + 1}: campo "song" obrigatório.')
            cleaned.append({
                'song': int(song_id),
                'order': int(item.get('order') or i + 1),
                'custom_key': (item.get('custom_key') or '').strip(),
                'notes': (item.get('notes') or '').strip(),
            })
        return cleaned