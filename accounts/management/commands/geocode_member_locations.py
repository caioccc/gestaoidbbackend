from django.core.management.base import BaseCommand, CommandError

from accounts import services
from accounts.models import Church, Member


class Command(BaseCommand):
    help = 'Geolocaliza endereços de membros e persiste as coordenadas encontradas.'

    def add_arguments(self, parser):
        parser.add_argument('--church-id', type=int, dest='church_id')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--retry-failed', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        queryset = Member.objects.filter(is_sentinel=False).order_by('id')
        if options['church_id']:
            if not Church.objects.filter(pk=options['church_id']).exists():
                raise CommandError('Igreja não encontrada.')
            queryset = queryset.filter(church_id=options['church_id'])
        if not options['force']:
            queryset = queryset.filter(
                latitude__isnull=True, longitude__isnull=True
            )
            if not options['retry_failed']:
                queryset = queryset.exclude(
                    geocode_status=Member.GeocodeStatus.NOT_FOUND
                )

        processed = 0
        located = 0
        failed = 0
        skipped = 0
        for member in queryset.iterator(chunk_size=500):
            if not services.member_location_query(member):
                skipped += 1
                continue
            if options['limit'] and processed >= options['limit']:
                break
            processed += 1
            if options['dry_run']:
                continue
            result = services.geocode_member(member, force=options['force'])
            if result['status'] == 'located':
                located += 1
                self.stdout.write(
                    f'[{member.id}] {member.name}: localizado '
                    f'({result["latitude"]}, {result["longitude"]})'
                )
            else:
                failed += 1
                self.stdout.write(
                    f'[{member.id}] {member.name}: {result["status"]}'
                )

        label = 'Simulação' if options['dry_run'] else 'Processados'
        self.stdout.write(self.style.SUCCESS(
            f'{label}: {processed} | Localizados: {located} | '
            f'Sem coords: {failed} | Sem endereço: {skipped}'
        ))
