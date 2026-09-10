import random
import unicodedata
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import (
    AccountingCategory,
    Church,
    ChurchMembership,
    ChurchMinutes,
    Loan,
    MaterialItem,
    Member,
    MemberRelative,
    MinistryArea,
    StorageLocation,
    WorshipService,
)
from finance.models import (
    CalendarEvent,
    DepartmentCategory,
    FinancialEntry,
    FinancialExit,
    MonthlyValidation,
    Tither,
    TitheRecord,
)
from finance.services import build_validation_checks, get_or_create_monthly_closing

User = get_user_model()

DEMO_PASSWORD = 'senha123'
DEMO_EMAIL = '@demo.idb'
CHURCH_MARKER = ' [Demo]'

YEAR = 2026
FINANCE_MONTHS = [6, 7, 8]
CLOSED_MONTH = 8

SEED_PERCENT = Decimal('10.00')

SEED_CATEGORY_KEYS = [
    *(f'CONGREGACAO_BAIRRO_{i:02d}' for i in range(1, 21)),
    'ESPECIAIS',
]

SEDE_NAME = f'Assembleia de Deus - Sede Central{CHURCH_MARKER}'
SEDE_CITY = 'João Pessoa'
SEDE_STATE = 'PB'

FIRST_NAMES = [
    'Maria', 'José', 'Ana', 'João', 'Antônio', 'Francisca', 'Carlos',
    'Adriana', 'Paulo', 'Luciana', 'Marcos', 'Fernanda', 'Raimundo',
    'Camila', 'Pedro', 'Juliana', 'Lucas', 'Patrícia', 'Gabriel', 'Beatriz',
    'Rafael', 'Larissa', 'Bruno', 'Amanda', 'Diego', 'Vanessa', 'Felipe',
    'Tatiane', 'Gustavo', 'Renata', 'Rodrigo', 'Natália', 'Thiago',
    'Priscila', 'Edson', 'Débora', 'Sérgio', 'Cristina', 'Wagner',
    'Silvana', 'Eduardo', 'Michele', 'Robson', 'Eliane', 'Marcelo',
    'Gisele', 'André', 'Simone', 'Alexandre', 'Mariana', 'Vitor',
    'Aline', 'Ricardo', 'Jéssica', 'Leonardo', 'Carolina', 'Fábio',
    'Sabrina', 'Renato', 'Mônica', 'Ítalo', 'Carla', 'Douglas', 'Erica',
    'Vinícius', 'Daiane', 'Everton', 'Sandra', 'Hugo', 'Tainá',
    'Samuel', 'Talita', 'César', 'Kelly', 'Mário', 'Pamela', 'Nelson',
    'Viviane', 'Oswaldo', 'Rosângela', 'Cláudio', 'Sueli', 'Jorge',
    'Angélica', 'Wilson', 'Irene', 'Otávio', 'Fátima', 'Igor', 'Gláucia',
    'Murilo', 'Lívia', 'Caio', 'Yasmin', 'Davi', 'Milena', 'Heitor',
    'Raquel', 'Breno', 'Lorena', 'Iago', 'Nara', 'Enzo', 'Ayla',
    'Tiago', 'Bruna', 'Alan', 'Raiane', 'Cícero', 'Doralice', 'Severina',
    'Lindolfo', 'Clotilde', 'Nivaldo', 'Zulmira', 'Arlindo', 'Genilda',
    'Cíntia', 'Wesley', 'Karla', 'Manoel',
]

SURNAMES = [
    'Silva', 'Santos', 'Oliveira', 'Souza', 'Lima', 'Pereira', 'Costa',
    'Rodrigues', 'Almeida', 'Nascimento', 'Carvalho', 'Araújo', 'Barbosa',
    'Gomes', 'Martins', 'Rocha', 'Moreira', 'Alves', 'Ribeiro', 'Melo',
    'Ferreira', 'Machado', 'Xavier', 'Ferraz', 'Teixeira', 'Correia',
    'Vieira', 'Coelho', 'Farias', 'Brito',
]

NEIGHBORHOODS = [
    'Bancários', 'Mangabeira', 'Jaguaribe', 'Cruz das Armas', 'Castelo Branco',
    'Miramar', 'Altiplano', 'Cabo Branco', 'Tambauzinho', 'Valentina',
]

PROFESSIONS = [
    'Professor(a)', 'Enfermeiro(a)', 'Comerciante', 'Autônomo(a)',
    'Pedreiro', 'Costureira', 'Motorista', 'Auxiliar Administrativo(a)',
    'Vendedor(a)', 'Doméstica', 'Mecânico', 'Advogado(a)', 'Contador(a)',
    'Padeiro', 'Cozinheira', 'Estudante', 'Aposentado(a)', 'Técnico(a) Enfermagem',
]

LEADERS = [
    'Pr. Carlos Alberto', 'Pb. Marcos Vinícius', 'Diác. João Batista',
    'Pr. Abílio Gomes', 'Ev. Paulo Serafim', 'Leitor Samuel André',
    'Pra. Josélia Andrade', 'Missionário Eliseu Mota',
]

SERMON_THEMES = [
    'O amor de Deus', 'A fé que move montanhas', 'Vivendo em santidade',
    'O poder da oração', 'Jovens no caminho', 'Família segundo o coração de Deus',
    'O fruto do Espírito', 'Esperança em tempos difíceis',
]

SCRIPTURES = [
    'João 3:16', 'Salmos 23', 'Mateus 28:19', 'Filipenses 4:13',
    'Romanos 12:1-2', '1 Coríntios 13',
]


def _slug(value: str) -> str:
    text = unicodedata.normalize('NFD', value.lower())
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return text.replace(' ', '.')


class Command(BaseCommand):
    help = (
        'Semeia dados de demonstração realistas e idempotentes (igrejas, '
        'usuários/RBAC, membresia, cultos, atas, patrimônio e financeiro). '
        'Passando --clean, remove apenas os dados criados pelo seed '
        '(usuários @demo.idb, igrejas com sufixo "[Demo]" e categorias do '
        'catálogo SEED) e volta a semear.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--clean',
            action='store_true',
            help=f'Remove os dados demo (usuários/senha padrão {DEMO_PASSWORD}) e semeia novamente.',
        )

    def handle(self, *args, **options):
        if options['clean']:
            self._clean()
            self.stdout.write(self.style.SUCCESS('Dados demo removidos. Re-semeando...'))
        self._seed_all()
        self.stdout.write(self.style.SUCCESS(
            f'Seed concluído. Usuários demo com senha "{DEMO_PASSWORD}".',
        ))

    @transaction.atomic
    def _clean(self):
        users = User.objects.filter(email__endswith=DEMO_EMAIL)
        user_count = users.count()
        users.delete()
        churches = Church.objects.filter(name__icontains=CHURCH_MARKER)
        church_count = churches.count()
        churches.delete()
        categories = AccountingCategory.objects.filter(key__in=SEED_CATEGORY_KEYS)
        category_count = categories.count()
        categories.delete()
        self.stdout.write(self.style.WARNING(
            f'[--clean] usuários={user_count}, igrejas={church_count}, '
            f'categorias={category_count} apagados.',
        ))

    @transaction.atomic
    def _seed_all(self):
        rng = random.Random(20260910)
        self._seed_categories()
        sede = self._seed_sede()
        congregations = self._seed_congregations(sede)
        self._seed_extra_churches()
        ministry_areas = self._seed_ministry_areas(sede)
        users = self._seed_users(sede, congregations)
        self._seed_members(sede, 110, 1, ministry_areas, rng)
        self._seed_members(congregations[0], 25, 1, ministry_areas, rng)
        self._seed_cultos(sede, rng)
        self._seed_atas(sede, users['pastor'])
        self._seed_inventory(sede, users['tesoureiro'])
        self._seed_calendar(sede)
        sede_tithers = self._seed_tithers(sede, 45, rng)
        self._seed_month_finance(sede, rng, congregation_mode=False)
        cong_tithers = self._seed_tithers(congregations[0], 10, rng)
        self._seed_month_finance(congregations[0], rng, congregation_mode=True)
        self._seed_closings_and_validations(sede, users['tesoureiro'])

    def _seed_categories(self):
        labels = {
            'ESPECIAIS': 'Repasse de Ofertas Especiais',
        }
        for key in SEED_CATEGORY_KEYS:
            AccountingCategory.objects.get_or_create(
                key=key,
                defaults={'label': labels.get(key, key.replace('_', ' ').title())},
            )

    def _seed_sede(self):
        sede, _ = Church.objects.get_or_create(
            name=SEDE_NAME,
            defaults=dict(
                church_type=Church.ChurchType.INDEPENDENT,
                parent_church=None,
                is_approved=True,
                status='ACTIVE',
                accounting_category=None,
                pastoral_prebenda_percent=SEED_PERCENT,
                pastor_name='Pr. Carlos Alberto',
                treasurer_name='João Barbosa de Lima',
                phone='(83) 99800-1234',
                cep='58030-410',
                street='Av. Dom Pedro II',
                number='1000',
                neighborhood='Centro',
                city=SEDE_CITY,
                state=SEDE_STATE,
                latitude=-7.1195,
                longitude=-34.8450,
                card_primary_color='#0f766e',
                card_secondary_color='#0ea5e9',
                card_valid_until=date(2027, 12, 31),
                card_front_phrase='Assembleia de Deus em João Pessoa',
                card_back_phrase='E nos dias que antecedem a volta de Jesus Cristo, sejamos luz para o mundo.',
            ),
        )
        return sede

    def _seed_congregations(self, sede):
        congregations = []
        for i in range(1, 21):
            name = f'Congregação do Bairro {i:02d}{CHURCH_MARKER}'
            cong, _ = Church.objects.get_or_create(
                name=name,
                defaults=dict(
                    church_type=Church.ChurchType.CONGREGATION,
                    parent_church=sede,
                    is_approved=True,
                    status='ACTIVE',
                    accounting_category=f'CONGREGACAO_BAIRRO_{i:02d}',
                    pastoral_prebenda_percent=Decimal('0'),
                    pastor_name=f'Pb. Responsável B{i:02d}',
                    treasurer_name=f'Tr. Financeiro B{i:02d}',
                    phone=f'(83) 9980{i:04d}',
                    cep='58030-000',
                    street=f'Rua das Palmeiras, {i}',
                    number=f'{i:02d}',
                    neighborhood=NEIGHBORHOODS[i % len(NEIGHBORHOODS)],
                    city=SEDE_CITY,
                    state=SEDE_STATE,
                ),
            )
            congregations.append(cong)
        return congregations

    def _seed_extra_churches(self):
        extra = [
            ('Jacaré', 'Campina Grande', 'PB'),
            ('Bela Vista', 'Bayeux', 'PB'),
        ]
        for area, city, state in extra:
            sede, _ = Church.objects.get_or_create(
                name=f'Igreja do {area}{CHURCH_MARKER}',
                defaults=dict(
                    church_type=Church.ChurchType.INDEPENDENT,
                    parent_church=None,
                    is_approved=True,
                    status='ACTIVE',
                    accounting_category=None,
                    pastoral_prebenda_percent=SEED_PERCENT,
                    pastor_name='Pr. Responsável Regional',
                    treasurer_name='Tr. Regional',
                    phone='(83) 99700-0000',
                    city=city,
                    state=state,
                ),
            )
            for cong_name in ('Aratu', 'Mirante'):
                Church.objects.get_or_create(
                    name=f'{cong_name}{CHURCH_MARKER}',
                    defaults=dict(
                        church_type=Church.ChurchType.CONGREGATION,
                        parent_church=sede,
                        is_approved=True,
                        status='ACTIVE',
                        accounting_category='ESPECIAIS',
                        pastoral_prebenda_percent=Decimal('0'),
                        pastor_name='Pb. Local',
                        treasurer_name='Tr. Local',
                        phone='(83) 99600-0000',
                        city=city,
                        state=state,
                    ),
                )

    def _seed_ministry_areas(self, sede):
        areas = []
        for name in [
            'Louvor e Música', 'Diaconato', 'Ministério Infantil',
            'Ministério de Jovens', 'Recepção e Boas-Vindas',
            'Ação Social', 'Ensino / Escola Bíblica',
        ]:
            area, _ = MinistryArea.objects.get_or_create(church=sede, name=name)
            areas.append(area)
        return areas

    @staticmethod
    def _make_user(email, name):
        user, _ = User.objects.get_or_create(email=email)
        user.name = name
        user.is_staff = False
        user.is_superuser = False
        user.is_active = True
        user.set_password(DEMO_PASSWORD)
        user.save()
        return user

    @staticmethod
    def _add_membership(user, church, role):
        ChurchMembership.objects.get_or_create(
            user=user, church=church, defaults={'role': role},
        )

    def _seed_users(self, sede, congregations):
        pastors = {
            'pastor': 'Carlos Alberto dos Santos',
            'tesoureiro': 'João Barbosa de Lima',
            'secretaria': 'Adriana de Fátima Nunes',
        }
        users = {}
        for key, name in pastors.items():
            user = self._make_user(f'{key}.sede{DEMO_EMAIL}', name)
            user.church = sede
            user.save()
            users[key] = user

        self._add_membership(users['pastor'], sede, ChurchMembership.Role.PASTOR)
        self._add_membership(users['tesoureiro'], sede, ChurchMembership.Role.TESOUREIRO)
        self._add_membership(users['secretaria'], sede, ChurchMembership.Role.SECRETARIA)

        for idx, cong in enumerate(congregations[:3], start=1):
            local_pastor = self._make_user(
                f'pastor.c{idx:02d}{DEMO_EMAIL}', f'Pb. Pastor Local {idx:02d}',
            )
            local_treasurer = self._make_user(
                f'tesoureiro.c{idx:02d}{DEMO_EMAIL}', f'Tr. Tesoureiro Local {idx:02d}',
            )
            for user in (local_pastor, local_treasurer):
                user.church = cong
                user.save()
            self._add_membership(local_pastor, cong, ChurchMembership.Role.PASTOR)
            self._add_membership(local_treasurer, cong, ChurchMembership.Role.TESOUREIRO)

        return users

    def _member_defaults(self, church, name, rng):
        city = church.city or SEDE_CITY
        state = church.state or SEDE_STATE
        birth_year = rng.randint(1952, 2008)
        birth_date = date(birth_year, rng.randint(1, 12), rng.randint(1, 28))
        baptism_date = None
        if rng.random() > 0.10:
            by = min(birth_year + rng.randint(4, 30), 2024)
            baptism_date = date(by, rng.randint(1, 12), rng.randint(1, 28))
        marital = rng.choice(Member.MaritalStatus.choices)[0]
        married = marital in (
            Member.MaritalStatus.CASADO, Member.MaritalStatus.UNIAO_ESTAVEL,
        )
        marriage_date = None
        if married:
            marriage_date = date(
                rng.randint(1980, 2024), rng.randint(1, 12), rng.randint(1, 28),
            )
        slug = _slug(name)
        return dict(
            name=name,
            phone=f'(83) 9{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}',
            email=f'{slug}@emailteste.com',
            birth_date=birth_date,
            baptism_date=baptism_date,
            cpf=f'{rng.randint(100, 999)}.{rng.randint(100, 999)}.{rng.randint(100, 999)}-{rng.randint(10, 99)}',
            rg=f'{rng.randint(1000000, 9999999)} SSP/{state}',
            born_in_city=rng.choice([city, 'Cajazeiras', 'Patos', 'Guarabira', 'Monteiro']),
            born_in_state=state,
            profession=rng.choice(PROFESSIONS),
            education_level=rng.choice(Member.EducationLevel.choices)[0],
            marital_status=marital,
            marriage_date=marriage_date,
            father_name=f'{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}',
            mother_name=f'{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}',
            church_entry=rng.choice(Member.ChurchEntry.choices)[0],
            street=rng.choice(['Rua', 'Av.']) + ' ' + rng.choice(['das Flores', 'Getúlio Vargas', 'Boa Vista', 'das Acácias']),
            number=f'{rng.randint(1, 1200)}',
            complement='',
            neighborhood=church.neighborhood or rng.choice(NEIGHBORHOODS),
            city=city,
            state=state,
            cep='58030-000',
            status=Member.Status.ACTIVE,
            notes='',
        )

    def _seed_members(self, church, count, start_card, ministry_areas, rng):
        members = []
        for i in range(count):
            card_number = f'{start_card + i:05d}'
            first = FIRST_NAMES[i % len(FIRST_NAMES)]
            middle = FIRST_NAMES[(i * 7 + len(FIRST_NAMES)) % len(FIRST_NAMES)]
            surname = SURNAMES[(i * 5) % len(SURNAMES)]
            if i % 3 == 0:
                surname += ' ' + SURNAMES[(i * 3 + 1) % len(SURNAMES)]
            name = f'{first} {middle} {surname}'.strip()
            member, created = Member.objects.get_or_create(
                church=church,
                card_number=card_number,
                defaults=self._member_defaults(church, name, rng),
            )
            if created:
                areas = rng.sample(ministry_areas, k=rng.randint(1, 3))
                member.ministry_areas.add(*areas)
            members.append(member)
        return members

    def _seed_cultos(self, church, rng):
        def mk(day, service_type, service_time):
            presider = rng.choice(LEADERS)
            preacher = rng.choice(LEADERS)
            WorshipService.objects.get_or_create(
                church=church,
                date=day,
                time=service_time,
                defaults=dict(
                    service_type=service_type,
                    presider=presider,
                    preacher=preacher,
                    theme=rng.choice(SERMON_THEMES),
                    scripture=rng.choice(SCRIPTURES),
                    attendees=rng.randint(120, 280),
                    visitors=rng.randint(3, 25),
                    conversions=rng.randint(0, 4),
                    offering=Decimal(str(rng.randint(350, 1400))),
                    notes='',
                    created_by=None,
                ),
            )

        cur = date(YEAR, 7, 1)
        stop = date(YEAR, 9, 1)
        while cur < stop:
            weekday = cur.weekday()
            if weekday == 6:
                mk(cur, WorshipService.ServiceType.CELEBRACAO, time(9, 0))
                mk(cur, WorshipService.ServiceType.CELEBRACAO, time(18, 0))
            elif weekday == 1:
                mk(cur, WorshipService.ServiceType.ORACAO, time(19, 30))
            elif weekday == 3:
                st = (
                    WorshipService.ServiceType.DOUTRINA
                    if cur.isocalendar().week % 2 == 0
                    else WorshipService.ServiceType.ESCOLA_BIBLICA
                )
                mk(cur, st, time(19, 30))
            cur += timedelta(days=1)

    def _seed_atas(self, sede, created_by):
        atas = [
            dict(
                title='Ata da Assembleia Geral Ordinária - Eleição da Diretoria',
                meeting_type=ChurchMinutes.MeetingType.ASSEMBLEIA_GERAL,
                meeting_date=date(YEAR, 2, 15),
                location='Templo Sede',
                recorder='Adriana de Fátima Nunes',
                participants='Presbíteros, diáconos, diaconisas e membresia em geral.',
                content=(
                    'Aos quinze dias do mês de fevereiro, reuniram-se os membros '
                    'da igreja. Após leitura da ata anterior e discussões, foi '
                    'eleita a nova diretoria para o biênio.'
                ),
            ),
            dict(
                title='Ata da Reunião da Diretoria - Planejamento Anual',
                meeting_type=ChurchMinutes.MeetingType.DIRETORIA,
                meeting_date=date(YEAR, 4, 10),
                location='Sala da Diretoria',
                recorder='José Firmino dos Santos',
                participants='Diretoria, pastor e tesouraria.',
                content=(
                    'Reunião para planejamento das atividades do ano, definição '
                    'de metas de arrecadação e organização dos ministérios.'
                ),
            ),
            dict(
                title='Ata da Assembleia Extraordinária - Eleição de Diáconos',
                meeting_type=ChurchMinutes.MeetingType.ASSEMBLEIA_EXTRAORDINARIA,
                meeting_date=date(YEAR, 6, 20),
                location='Templo Sede',
                recorder='Adriana de Fátima Nunes',
                participants='Membresia com direito a voto.',
                content=(
                    'Assembleia extraordinária para eleição de novos diáconos '
                    'e diaconisas para atender as congregações.'
                ),
            ),
            dict(
                title='Ata da Assembleia - Prestação de Contas Anual',
                meeting_type=ChurchMinutes.MeetingType.ASSEMBLEIA_GERAL,
                meeting_date=date(YEAR, 8, 16),
                location='Templo Sede',
                recorder='João Barbosa de Lima',
                participants='Membresia e conselho fiscal.',
                content=(
                    'Apresentação e votação da prestação de contas do exercício, '
                    'com parecer do conselho fiscal aprovado por unanimidade.'
                ),
            ),
        ]
        for data in atas:
            ChurchMinutes.objects.get_or_create(
                church=sede,
                title=data['title'],
                defaults=dict(
                    meeting_type=data['meeting_type'],
                    meeting_date=data['meeting_date'],
                    location=data['location'],
                    recorder=data['recorder'],
                    participants=data['participants'],
                    content=data['content'],
                    created_by=created_by,
                ),
            )

    def _seed_inventory(self, sede, created_by):
        locations = {}
        for name in [
            'Templo Principal', 'Galpão de Som', 'Sala da EBD',
            'Cozinha', 'Sala da Diretoria',
        ]:
            loc, _ = StorageLocation.objects.get_or_create(church=sede, name=name)
            locations[name] = loc

        def item(name, description, location):
            MaterialItem.objects.get_or_create(
                church=sede,
                name=name,
                defaults=dict(description=description, location=location),
            )

        item('Caixa de som JBL EON 615', 'Par de caixas ativas 15 polegadas', locations['Galpão de Som'])
        item('Console de som Behringer X32', 'Mesa de som digital 32 canais', locations['Galpão de Som'])
        item('Microfones sem fio (par)', '2x mic dinâmico UHF', locations['Galpão de Som'])
        item('Guitarra Strinberg', 'Guitarra elétrica coral', locations['Galpão de Som'])
        item('Teclado Yamaha PSR-S670', 'Teclado arranjador', locations['Galpão de Som'])
        item('Projetor Epson EB-X51', 'Projetor multimídia 3600 lúmens', locations['Templo Principal'])
        item('Tela de projeção 120"', 'Tela retrátil para cultos', locations['Templo Principal'])
        item('Cadeiras de plástico (caixa c/ 10)', 'Mobiliário do templo', locations['Templo Principal'])
        item('Mesas de madeira (pares)', 'Uso em eventos', locations['Templo Principal'])
        item('Bebedouro de coluna', 'Refrigeração 20L', locations['Cozinha'])
        item('Fogão industrial 6 bocas', 'Cozinha da igreja', locations['Cozinha'])
        item('Freezer horizontal', 'Armazenamento de alimentos', locations['Cozinha'])
        item('Bíblia de púlpito', 'Acervo do púlpito', locations['Templo Principal'])
        item('Microcomputador de mesa', 'Secretaria', locations['Sala da Diretoria'])
        item('Notebook Lenovo', 'Uso da coordenação', locations['Sala da Diretoria'])
        item('Impressora multifuncional', 'Secretaria / Diretoria', locations['Sala da Diretoria'])
        item('Data show (reserva)', 'Suporte a eventos', locations['Galpão de Som'])

        items = {i.name: i for i in MaterialItem.objects.filter(church=sede)}
        members = list(Member.objects.filter(church=sede).order_by('id')[:8])

        loan_specs = [
            dict(
                item='Data show (reserva)', member=members[0], borrower_name='',
                borrowed=date(YEAR, 2, 10), expected=date(YEAR, 2, 17),
                returned=timezone.make_aware(datetime.combine(date(YEAR, 2, 17), time(18, 30))),
                notes='Seminário regional',
            ),
            dict(
                item='Cadeiras de plástico (caixa c/ 10)', member=members[1], borrower_name='',
                borrowed=date(YEAR, 8, 20), expected=date(YEAR, 8, 27),
                returned=timezone.make_aware(datetime.combine(date(YEAR, 8, 27), time(20, 0))),
                notes='Festa de aniversário',
            ),
            dict(
                item='Projetor Epson EB-X51', member=members[2], borrower_name='',
                borrowed=date(YEAR, 9, 1), expected=date(YEAR, 9, 30),
                returned=None, notes='Evento da EBD',
            ),
            dict(
                item='Guitarra Strinberg', member=None,
                borrower_name='Igreja Batista Betel',
                borrowed=date(YEAR, 8, 1), expected=date(YEAR, 8, 15),
                returned=None, notes='Emprestada para evento',
            ),
        ]
        for spec in loan_specs:
            Loan.objects.get_or_create(
                church=sede,
                item=items[spec['item']],
                member=spec['member'],
                borrower_name=spec['borrower_name'],
                borrowed_at=spec['borrowed'],
                defaults=dict(
                    expected_return=spec['expected'],
                    returned_at=spec['returned'],
                    notes=spec['notes'],
                    created_by=created_by,
                ),
            )

    def _seed_calendar(self, sede):
        recurring = [
            ('Energisa', CalendarEvent.Category.BILL, 10),
            ('Cagepa (água)', CalendarEvent.Category.BILL, 15),
            ('Internet', CalendarEvent.Category.BILL, 20),
            ('Remessa Regional', CalendarEvent.Category.DEADLINE, 5),
        ]
        for title, category, day in recurring:
            CalendarEvent.objects.get_or_create(
                church=sede,
                title=title,
                category=category,
                audience=CalendarEvent.Audience.FINANCE,
                repeat_monthly=True,
                day=day,
                defaults=dict(description='', month=None, date=None),
            )

        general = [
            dict(title='Reunião da Diretoria Trimestral', category=CalendarEvent.Category.MEETING,
                 date=date(YEAR, 9, 18), start=time(19, 30), description='Pauta orçamentária'),
            dict(title='Culto de Ceia do Senhor', category=CalendarEvent.Category.CULTO,
                 date=date(YEAR, 9, 27), start=time(18, 0), description='Ceia mensal'),
            dict(title='Ensaio do Coral', category=CalendarEvent.Category.ENSAIO,
                 date=date(YEAR, 9, 14), start=time(19, 0), description='Preparação para o Louvor'),
            dict(title='Jantar de Confraternização', category=CalendarEvent.Category.EVENT,
                 date=date(YEAR, 9, 26), start=time(19, 30), description='Famílias'),
            dict(title='Culto de Missões', category=CalendarEvent.Category.CULTO,
                 date=date(YEAR, 9, 12), start=time(19, 30), description='Missionários convidados'),
        ]
        for data in general:
            CalendarEvent.objects.get_or_create(
                church=sede,
                title=data['title'],
                audience=CalendarEvent.Audience.GENERAL,
                repeat_monthly=False,
                date=data['date'],
                defaults=dict(
                    category=data['category'],
                    start_time=data['start'],
                    description=data['description'],
                    month=None,
                    day=None,
                ),
            )

        weekly = [
            dict(title='Culto Dominical', category=CalendarEvent.Category.CULTO,
                 weekdays=[6], interval=1, anchor=date(YEAR, 9, 6), start=time(18, 0),
                 description='Culto de celebração'),
            dict(title='Escola Bíblica Dominical (EBD)', category=CalendarEvent.Category.CULTO,
                 weekdays=[6], interval=1, anchor=date(YEAR, 9, 6), start=time(17, 0),
                 description='Estudo bíblico'),
            dict(title='Culto de Ensino', category=CalendarEvent.Category.CULTO,
                 weekdays=[4], interval=1, anchor=date(YEAR, 9, 10), start=time(19, 0),
                 description='Culto de doutrina'),
            dict(title='Ensaio da Banda Frutos do Espírito', category=CalendarEvent.Category.ENSAIO,
                 weekdays=[0], interval=1, anchor=date(YEAR, 9, 7), start=time(20, 0),
                 description='Ensaio da banda'),
            dict(title='Matutino de Oração', category=CalendarEvent.Category.CULTO,
                 weekdays=[3], interval=1, anchor=date(YEAR, 9, 9), start=time(5, 0),
                 description='Oração da madrugada'),
            dict(title='Círculo de Oração', category=CalendarEvent.Category.MEETING,
                 weekdays=[3], interval=1, anchor=date(YEAR, 9, 9), start=time(14, 0),
                 description='Reunião de oração das irmãs'),
            dict(title='Culto Gilgal (Jovens)', category=CalendarEvent.Category.CULTO,
                 weekdays=[3], interval=1, anchor=date(YEAR, 9, 9), start=time(20, 0),
                 description='Culto da mocidade'),
            dict(title='Ensaio do Grupo Getsêmani', category=CalendarEvent.Category.ENSAIO,
                 weekdays=[5], interval=1, anchor=date(YEAR, 9, 4), start=time(19, 30),
                 description='Ensaio do grupo de louvor'),
            dict(title='Ensaio da Banda Águia da Paz', category=CalendarEvent.Category.ENSAIO,
                 weekdays=[6], interval=2, anchor=date(YEAR, 9, 6), start=time(9, 0),
                 end=time(12, 0), description='Ensaio da banda que tocará no culto da noite'),
            dict(title='Ensaio da Banda Siloé', category=CalendarEvent.Category.ENSAIO,
                 weekdays=[6], interval=2, anchor=date(YEAR, 9, 13), start=time(9, 0),
                 end=time(12, 0), description='Ensaio da banda que tocará no culto da noite'),
        ]
        for data in weekly:
            CalendarEvent.objects.get_or_create(
                church=sede,
                title=data['title'],
                audience=CalendarEvent.Audience.GENERAL,
                repeat_weekly=True,
                weekdays=data['weekdays'],
                repeat_interval=data['interval'],
                date=data['anchor'],
                defaults=dict(
                    category=data['category'],
                    repeat_monthly=False,
                    start_time=data['start'],
                    end_time=data.get('end'),
                    description=data['description'],
                    month=None,
                    day=None,
                ),
            )

    def _seed_tithers(self, church, count, rng):
        names = ['Mariazinha', 'Zé Chico', 'Bastião', 'Dona Guiomar', 'Seu Luiz',
                 'Zefa', 'Tião', 'Raimundinho', 'Dona Fátima', 'Seu Antônio']
        idx = 0
        while len(names) < count:
            first = FIRST_NAMES[idx % len(FIRST_NAMES)]
            surname = SURNAMES[(idx * 11) % len(SURNAMES)]
            candidate = f'{first} {surname}'
            if candidate not in names:
                names.append(candidate)
            idx += 1

        tithers = []
        for name in names:
            tither, _ = Tither.objects.get_or_create(church=church, name=name)
            tithers.append(tither)

        skip_from = count - int(count * 0.10)
        for i, tither in enumerate(tithers):
            base = Decimal(str(rng.randint(60, 420)))
            for month in FINANCE_MONTHS:
                if month == CLOSED_MONTH and i >= skip_from:
                    continue
                amount = base + Decimal(str((i * 13 + month) % 47))
                TitheRecord.objects.get_or_create(
                    tither=tither, year=YEAR, month=month,
                    defaults={'amount': amount},
                )
        return tithers

    @staticmethod
    def _sum_tithe_month(church, month):
        return (
            TitheRecord.objects.filter(
                tither__church=church, year=YEAR, month=month,
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )

    def _seed_month_finance(self, church, rng, *, congregation_mode):
        for month in FINANCE_MONTHS:
            dizimo_total = self._sum_tithe_month(church, month)
            FinancialEntry.objects.update_or_create(
                church=church,
                date=date(YEAR, month, 1),
                service_description='Dízimos mensais',
                category=DepartmentCategory.DIZIMO,
                defaults={'amount': dizimo_total},
            )

            sundays = [
                day for day in (
                    date(YEAR, month, 1) + timedelta(days=n) for n in range(31)
                )
                if day.month == month and day.weekday() == 6
            ]
            for sunday_index, sunday in enumerate(sundays, start=1):
                FinancialEntry.objects.update_or_create(
                    church=church,
                    date=sunday,
                    service_description=f'Oferta do {sunday_index}º Domingo',
                    category=DepartmentCategory.OFERTA,
                    defaults={'amount': Decimal(str(rng.randint(700, 1600)))},
                )

            departments = [
                (DepartmentCategory.MULHERES, 'Oferta Ministério de Mulheres', (120, 400)),
                (DepartmentCategory.HOMENS, 'Oferta Ministério de Homens', (120, 400)),
                (DepartmentCategory.JOVENS, 'Oferta Ministério de Jovens', (120, 400)),
                (DepartmentCategory.ESC_BIBLICA, 'Oferta Escola Bíblica', (120, 400)),
                (DepartmentCategory.INFANTIL, 'Oferta Ministério Infantil', (80, 250)),
                (DepartmentCategory.ADOLESCENTES, 'Oferta Adolescentes', (80, 250)),
                (DepartmentCategory.CASAIS, 'Oferta Ministério de Casais', (100, 300)),
                (DepartmentCategory.MISSOES, 'Oferta Missionária', (150, 500)),
                (DepartmentCategory.CONSTRUCAO, 'Campanha de Construção', (500, 1000)),
                (DepartmentCategory.ESPECIAL, 'Oferta Especial', (200, 600)),
            ]
            if not congregation_mode:
                departments.append(
                    (DepartmentCategory.VISAO_CORPORATIVA, 'Visão Corporativa', (150, 350)),
                )
            for index, (category, description, (low, high)) in enumerate(departments, start=1):
                FinancialEntry.objects.update_or_create(
                    church=church,
                    date=date(YEAR, month, min(1 + index, 28)),
                    service_description=description,
                    category=category,
                    defaults={'amount': Decimal(str(rng.randint(low, high)))},
                )

            exit_specs = [
                ('Energisa', (30000, 39000), date(YEAR, month, 10)),
                ('Cagepa (água)', (10500, 14000), date(YEAR, month, 15)),
                ('Internet fibra', (14990, 14990), date(YEAR, month, 20)),
                ('Zeladoria', (68000, 68000), date(YEAR, month, 5)),
                ('Secretaria', (42000, 42000), date(YEAR, month, 5)),
                ('Ajuda de custo - pregador convidado', (15000, 30000), date(YEAR, month, 12)),
            ]
            if not congregation_mode:
                exit_specs.append(('Manutenção do som', (15000, 26000), date(YEAR, month, 18)))
            for description, (low, high), day in exit_specs:
                FinancialExit.objects.update_or_create(
                    church=church,
                    date=day,
                    description=description,
                    category=DepartmentCategory.ESPECIAL,
                    defaults={'amount': Decimal(str(rng.randint(low, high))) / Decimal('100')},
                )

            if not congregation_mode:
                month_entries = (
                    FinancialEntry.objects.filter(
                        church=church, date__year=YEAR, date__month=month,
                    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
                )
                expected = (month_entries * SEED_PERCENT / Decimal('100')).quantize(Decimal('0.01'))
                FinancialExit.objects.update_or_create(
                    church=church,
                    date=date(YEAR, month, 28),
                    description='Prebenda Pastoral',
                    category=DepartmentCategory.ESPECIAL,
                    defaults={'amount': expected},
                )

    def _seed_closings_and_validations(self, sede, tesoureiro):
        for month in FINANCE_MONTHS:
            closing, _ = get_or_create_monthly_closing(sede, YEAR, month)
            if month == CLOSED_MONTH:
                closing.is_closed = True
                closing.save()

        leadership = User.objects.filter(
            is_staff=True, is_superuser=True,
        ).order_by('id').first()

        for month in FINANCE_MONTHS:
            checks = build_validation_checks(sede, YEAR, month)
            validation, _ = MonthlyValidation.objects.get_or_create(
                church=sede, year=YEAR, month=month,
            )
            validation.checks = checks
            if month == CLOSED_MONTH:
                now = timezone.now()
                validation.approved_by_treasury = tesoureiro
                validation.treasury_approved_at = now
                if leadership is not None:
                    validation.approved_by_leadership = leadership
                    validation.leadership_approved_at = now
                validation.note = 'Competência fechada e validada (dados demo).'
                validation.recompute_status()
            validation.save()