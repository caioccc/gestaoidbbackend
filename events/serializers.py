from rest_framework import serializers

from accounts.services import cloudinary_url

from .models import (
    Event,
    EventCheckin,
    EventCheckinConfig,
    EventCheckinTeamMember,
    EventRegistration,
    EventSession,
)
from .services import event_for


def person_payload(member):
    return {
        'id': member.pk,
        'name': member.name,
        'card_number': member.card_number or '',
        'photo': cloudinary_url(member.photo) if member.photo else '',
        'church_name': member.church.name if member.church_id else '',
        'status': member.status,
    }


def registration_payload(registration):
    if registration.kind == EventRegistration.Kind.MEMBER and registration.member_id:
        payload = person_payload(registration.member)
        payload['kind'] = 'MEMBER'
        payload['code'] = registration.code
        return payload
    return {
        'id': registration.pk,
        'name': registration.display_name,
        'card_number': registration.code,
        'code': registration.code,
        'photo': cloudinary_url(registration.photo) if registration.photo else '',
        'church_name': '',
        'status': registration.status,
        'kind': 'EXTERNAL',
    }


class EventSerializer(serializers.ModelSerializer):
    calendar_event_title = serializers.CharField(
        source='calendar_event.title', read_only=True,
    )
    registration_url = serializers.CharField(read_only=True)
    registration_count = serializers.SerializerMethodField()
    checked_count = serializers.SerializerMethodField()
    checkin_config_id = serializers.SerializerMethodField()
    source_display = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model = Event
        fields = [
            'id', 'church', 'source', 'source_display', 'calendar_event',
            'calendar_event_title', 'title', 'date', 'start_time', 'end_time',
            'location', 'description', 'registration_token',
            'registration_token_is_active', 'registration_url', 'is_active',
            'registration_count', 'checked_count', 'checkin_config_id',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'church', 'registration_token', 'registration_url',
            'registration_count', 'checked_count', 'checkin_config_id',
            'created_at', 'updated_at',
        ]

    def get_registration_count(self, obj):
        return obj.registrations.filter(status=EventRegistration.Status.ACTIVE).count()

    def get_checked_count(self, obj):
        return obj.checkin_sessions.filter(
            checkins__status=EventCheckin.Status.CHECKED_IN
        ).count()

    def get_checkin_config_id(self, obj):
        config = getattr(obj, 'checkin_config', None)
        return config.pk if config else None


class EventRegistrationSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    card_number = serializers.CharField(read_only=True)
    qr_url = serializers.CharField(read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    source_display = serializers.CharField(source='get_source_display', read_only=True)
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    member_name = serializers.CharField(source='member.name', read_only=True)

    class Meta:
        model = EventRegistration
        fields = [
            'id', 'church', 'event', 'kind', 'kind_display', 'member', 'member_name',
            'name', 'display_name', 'phone', 'email', 'code', 'card_number',
            'qr_url', 'status', 'status_display', 'source', 'source_display',
            'registered_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'church', 'code', 'qr_url', 'display_name', 'card_number',
            'registered_at', 'created_at', 'updated_at',
        ]


class BulkMembersSerializer(serializers.Serializer):
    members = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list,
    )
    all_members = serializers.BooleanField(default=False)


class PublicEventRegistrationMetaSerializer(serializers.Serializer):
    token = serializers.CharField()
    event_id = serializers.IntegerField()
    event_title = serializers.CharField()
    church_name = serializers.CharField()
    date = serializers.DateField(allow_null=True)
    start_time = serializers.TimeField(allow_null=True)
    end_time = serializers.TimeField(allow_null=True)
    location = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    is_open = serializers.BooleanField()


class PublicEventRegistrationPayloadSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, trim_whitespace=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)


class PublicEventRegistrationResultSerializer(serializers.Serializer):
    created = serializers.BooleanField()
    already = serializers.BooleanField()
    code = serializers.CharField()
    qr_url = serializers.CharField()
    name = serializers.CharField()
    event_title = serializers.CharField()


class EventCheckinConfigSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(read_only=True)
    category_display = serializers.SerializerMethodField()
    session_count = serializers.SerializerMethodField()
    team_count = serializers.SerializerMethodField()
    registration_count = serializers.SerializerMethodField()

    class Meta:
        model = EventCheckinConfig
        fields = [
            'id', 'church', 'event', 'checkin_event', 'event_title',
            'category_display', 'mode', 'checkin_opens_at', 'checkin_closes_at',
            'generation_start_date', 'generation_end_date', 'is_active',
            'session_count', 'team_count', 'registration_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['church', 'checkin_event', 'created_at', 'updated_at']

    def get_category_display(self, obj):
        event = event_for(obj)
        if event and event.calendar_event_id:
            return event.calendar_event.get_category_display()
        return ''

    def get_session_count(self, obj):
        return obj.sessions.count()

    def get_team_count(self, obj):
        return obj.team_members.count()

    def get_registration_count(self, obj):
        event = event_for(obj)
        if event is None:
            return 0
        return event.registrations.filter(
            status=EventRegistration.Status.ACTIVE
        ).count()


class EventSessionSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(read_only=True)
    effective_opens_at = serializers.DateTimeField(read_only=True)
    effective_closes_at = serializers.DateTimeField(read_only=True)
    is_open = serializers.SerializerMethodField()
    checkin_count = serializers.SerializerMethodField()

    class Meta:
        model = EventSession
        fields = [
            'id', 'config', 'event', 'checkin_event', 'event_title', 'church',
            'occurrence_date', 'token', 'token_is_active',
            'opens_at', 'closes_at', 'effective_opens_at', 'effective_closes_at',
            'is_open', 'checkin_count', 'created_at',
        ]
        read_only_fields = ['church', 'event', 'checkin_event', 'config', 'token', 'created_at']

    def get_is_open(self, obj):
        return obj.is_open()

    def get_checkin_count(self, obj):
        return obj.checkins.filter(status=EventCheckin.Status.CHECKED_IN).count()


class EventCheckinTeamMemberSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source='member.name', read_only=True)
    card_number = serializers.CharField(source='member.card_number', read_only=True)
    photo = serializers.SerializerMethodField()
    role_display = serializers.CharField(
        source='get_role_display', read_only=True,
    )

    class Meta:
        model = EventCheckinTeamMember
        fields = [
            'id', 'config', 'church', 'member', 'member_name', 'card_number',
            'photo', 'role', 'role_display', 'created_at',
        ]
        read_only_fields = ['church', 'created_at']

    def get_photo(self, obj):
        return cloudinary_url(obj.member.photo) if obj.member.photo else ''


class EventCheckinPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    card_number = serializers.CharField(allow_blank=True)
    photo = serializers.CharField(allow_blank=True)
    church_name = serializers.CharField(allow_blank=True)
    status = serializers.CharField(allow_blank=True)
    kind = serializers.CharField(required=False, allow_blank=True)


class EventCheckinRowSerializer(serializers.ModelSerializer):
    occurrence_date = serializers.DateField(source='session.occurrence_date', read_only=True)
    event_id = serializers.SerializerMethodField()
    event_title = serializers.CharField(source='session.event_title', read_only=True)
    member = serializers.SerializerMethodField()
    person = serializers.SerializerMethodField()
    registration = serializers.IntegerField(
        source='registration_id', allow_null=True, read_only=True,
    )
    registration_kind = serializers.CharField(
        source='registration.get_kind_display', read_only=True, allow_null=True,
    )
    registration_source = serializers.CharField(
        source='registration.get_source_display', read_only=True, allow_null=True,
    )
    code = serializers.CharField(source='registration.code', read_only=True, allow_null=True)
    operator_name = serializers.SerializerMethodField()
    role_name = serializers.SerializerMethodField()
    source_display = serializers.CharField(source='get_source_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = EventCheckin
        fields = [
            'id', 'session', 'occurrence_date', 'event_id', 'event_title',
            'member', 'person', 'registration', 'registration_kind',
            'registration_source', 'code', 'in_roster', 'source', 'source_display',
            'status', 'status_display', 'operator', 'operator_name', 'role_name',
            'checked_at', 'updated_at',
        ]

    def get_uniqueness_extra_kwargs(self, field_names, declared_fields, extra_kwargs):
        return extra_kwargs, {}

    def get_event_id(self, obj):
        event = event_for(obj.session)
        return event.pk if event else None

    def get_member(self, obj):
        if obj.member_id:
            return person_payload(obj.member)
        return None

    def get_person(self, obj):
        if obj.registration_id:
            return registration_payload(obj.registration)
        if obj.member_id:
            return person_payload(obj.member)
        return None

    def get_operator_name(self, obj):
        if not obj.operator:
            return ''
        return obj.operator.member.name

    def get_role_name(self, obj):
        if not obj.operator:
            return ''
        return obj.operator.get_role_display()


class PublicEventSessionSerializer(serializers.Serializer):
    token = serializers.CharField()
    event_id = serializers.IntegerField()
    event_title = serializers.CharField()
    church_name = serializers.CharField()
    occurrence_date = serializers.DateField()
    start_time = serializers.TimeField(allow_null=True)
    end_time = serializers.TimeField(allow_null=True)
    mode = serializers.CharField()
    is_open = serializers.BooleanField()
    team = serializers.ListField(child=serializers.DictField())


class CheckinScanSerializer(serializers.Serializer):
    code = serializers.CharField(allow_blank=False, trim_whitespace=True)
    operator = serializers.IntegerField(required=False, allow_null=True)
    source = serializers.ChoiceField(
        choices=EventCheckin.Source.choices, default=EventCheckin.Source.SCAN,
    )


class CheckinManualSerializer(serializers.Serializer):
    session = serializers.IntegerField(required=False)
    member = serializers.IntegerField(required=False, allow_null=True)
    registration = serializers.IntegerField(required=False, allow_null=True)
    operator = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs.get('member') and not attrs.get('registration'):
            raise serializers.ValidationError(
                {'detail': 'Informe um membro ou uma inscrição.'}
            )
        return attrs
