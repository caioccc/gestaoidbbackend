import calendar
import random
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import (
    CertificateTemplate,
    Church,
    ChurchMembership,
    ChurchMinutes,
    EcclesiasticalCertificate,
    GrowthGroup,
    Loan,
    MaterialItem,
    Member,
    PastoralVisit,
    PrayerRequest,
    StorageLocation,
    SundaySchoolAttendance,
    SundaySchoolClass,
    SundaySchoolEnrollment,
    SundaySchoolSession,
    WorshipService,
)
from accounts.models import User

MONTHS = [
    'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]

FIRST_NAMES = [
    'Ana', 'Beatriz', 'Carlos', 'Daniel', 'Elisa', 'Fábio', 'Gabriela',
    'Helena', 'Igor', 'Júlia', 'Karla', 'Lucas', 'Mariana', 'Nicolas',
    'Otávio', 'Patrícia', 'Rafael', 'Sabrina', 'Thiago', 'Valéria',
    'William', 'Yasmin', 'Bruno', 'Camila', 'Diego', 'Eduarda', 'Felipe',
    'Gustavo', 'Isabela', 'João', 'Larissa', 'Mateus',
]

LAST_NAMES = [
    'Almeida', 'Barbosa', 'Cardoso', 'Dantas', 'Ferreira', 'Gomes',
    'Lima', 'Melo', 'Nascimento', 'Oliveira', 'Pereira', 'Santos',
    'Silva', 'Souza', 'Cavalcanti', 'Araújo', 'Costa', 'Nunes',
]

BARRIS_CAMPINA = [
    'Centro', 'Catolé', 'Bodocongó', 'Prata', 'Cruzeiro', 'Liberdade',
    'Alto Branco', 'Acácio Figueiredo', 'Bela Vista', 'São José',
    'Palmeira', 'Monte Castelo', 'José Pinheiro', 'Cuités', 'Malvinas',
    'Ramadinha', 'Nova Brasília', 'Distrito Industrial',
]

PRAYER_DESCRIPTIONS = [
    'Solicita oração por restauração da saúde após cirurgia.',
    'Pedido de oração pela família e pela reconciliação dos filhos.',
    'Oração por direção profissional e novas oportunidades de trabalho.',
    'Intercessão pela conversão de um ente querido.',
    'Pedido de oração por paz e alívio da ansiedade.',
    'Oração pela recuperação do cônjuge em tratamento médico.',
    'Pedido de oração pelo cuidado com os pais idosos.',
    'Oração por sabedoria nas decisões financeiras da família.',
    'Intercessão pela união e comunhão do grupo familiar.',
    'Pedido de oração pelo empreendimento que está começando.',
    'Oração pela colheita e proteção da lavoura da família.',
    'Pedido de oração por um filho distante e em dificuldades.',
    'Intercessão pela restauração de um casamento em crise.',
    'Pedido de oração pela superação de uma perda recente.',
    'Oração por proteção nas viagens e no trabalho.',
    'Pedido de oração pela juventude da igreja local.',
]

SERVICE_TYPES = [
    'CELEBRACAO', 'DOUTRINA', 'ORACAO', 'VIGILIA',
    'CEIA', 'ESCOLA_BIBLICA', 'JOVENS', 'OUTRO',
]
SERVICE_THEMES = [
    'O poder da Palavra',
    'Vivendo pela fé',
    'A graça que transforma',
    'Oração que move o impossível',
    'Família segundo o coração de Deus',
    'Servindo com excelência',
    'Esperança que não decepciona',
    'Comunhão e unidade no Corpo',
    'A alegria do Senhor é a nossa força',
    'Portas abertas, corações rendidos',
    'Renovação espiritual',
    'A igreja que edifica',
]
PREACHERS = [
    'Pr. Carlos Alberto dos Santos', 'Pb. José Maria Ferreira',
    'Pb. Ricardo Nunes', 'Ev. Marcos Dantas', 'Pb. Antônio Souza',
    'Ev. Paula Almeida',
]
PRESIDERS = [
    'Diác. João Barbosa', 'Diác. Pedro Pereira', 'Diác. André Melo',
    'Diác. Samuel Costa',
]

LIDERANCA_BODY = """No dia {{DIA}} de {{MES_EXTENSO}} de {{ANO}}, às {{HORA}} horas, nas dependências da {{NOME_IGREJA}}, reuniu-se a liderança ministerial e dos departamentos, sob a coordenação do(a) {{PRESIDENTE}}, com a secretaria do(a) {{REDATOR}}.

1. DA ABERTURA E ORAÇÃO INICIAL:
A reunião foi aberta com leitura bíblica em [Texto Bíblico] e oração inicial por [Nome].

2. DA PAUTA:
a) Alinhamento ministerial e visão da igreja;
b) Informes dos departamentos e ministérios;
c) Calendário, eventos e programações;
d) [Assuntos Gerais].

3. DAS DECISÕES E ENCAMINHAMENTOS:
[Registrar as decisões tomadas, os responsáveis e os prazos acordados].

4. DO ENCERRAMENTO:
Nada mais havendo a tratar, a reunião foi encerrada às {{HORA_TERMINO}} horas com oração final [Nome]. Eu, {{REDATOR}}, lavrei a presente ata, que segue assinada por mim e pelo(a) {{PRESIDENTE}}.

{{CIDADE_UF}}, {{DATA_FORMATADA}}.

___________________________________________
{{PRESIDENTE}}
Presidente da Reunião

___________________________________________
{{REDATOR}}
Secretário(a)"""

DIRETORIA_BODY = """No dia {{DIA}} de {{MES_EXTENSO}} de {{ANO}}, às {{HORA}} horas, nas dependências da {{NOME_IGREJA}}, reuniu-se a Diretoria no exercício de suas atribuições, sob a coordenação do(a) {{PRESIDENTE}}, com a secretaria do(a) {{REDATOR}}.

1. DA ABERTURA E ORAÇÃO INICIAL:
A reunião foi aberta com leitura bíblica em [Texto Bíblico] e oração inicial por [Nome].

2. DA PAUTA E PLANEJAMENTO:
a) Informes e pendências da reunião anterior;
b) Planejamento ministerial e calendário de atividades;
c) Proposições, orçamento e prestação de contas;
d) [Assuntos Gerais].

3. DAS DECISÕES E ENCAMINHAMENTOS:
[Registrar as decisões tomadas, os responsáveis e os prazos acordados].

4. DO ENCERRAMENTO:
Nada mais havendo a tratar, a reunião foi encerrada às {{HORA_TERMINO}} horas com oração final [Nome]. Eu, {{REDATOR}}, lavrei a presente ata, que segue assinada por mim e pelo(a) {{PRESIDENTE}}.

{{CIDADE_UF}}, {{DATA_FORMATADA}}.

___________________________________________
{{PRESIDENTE}}
Presidente da Reunião

___________________________________________
{{REDATOR}}
Secretário(a)"""

CONSELHO_BODY = """No dia {{DIA}} de {{MES_EXTENSO}} de {{ANO}}, às {{HORA}} horas, nas dependências da {{NOME_IGREJA}}, reuniu-se o Conselho Fiscal, sob a presidência do(a) {{PRESIDENTE}}, com a secretaria do(a) {{REDATOR}}, para o exame das contas e da prestação de responsabilidade da tesouraria.

1. DA ABERTURA E ORAÇÃO INICIAL:
A reunião foi aberta com oração inicial por [Nome] e declaração dos trabalhos pelo(a) Presidente.

2. DO EXAME DOS LIVROS E BALANCETES:
O Conselho procedeu à conferência dos livros contábeis e dos balancetes apresentados pela tesouraria, relativos ao período [Período], verificando-se a documentação de suporte das receitas e despesas.

3. DAS CONSIDERAÇÕES E PARECER:
Concluída a conferência, o Conselho decidiu [aprovar/rejeitar] as contas apresentadas, nos seguintes termos: [registrar considerações, ressalvas ou recomendações].

4. DO ENCERRAMENTO:
Nada mais havendo a tratar, a reunião foi encerrada às {{HORA_TERMINO}} horas. Eu, {{REDATOR}}, lavrei a presente ata, que segue assinada por mim e pelo(a) {{PRESIDENTE}}.

{{CIDADE_UF}}, {{DATA_FORMATADA}}.

___________________________________________
{{PRESIDENTE}}
Presidente da Reunião

___________________________________________
{{REDATOR}}
Secretário(a)"""

ASSEMBLEIA_GERAL_BODY = """No dia {{DIA}} de {{MES_EXTENSO}} de {{ANO}}, às {{HORA}} horas, nas dependências da {{NOME_IGREJA}}, situada à {{ENDERECO_IGREJA}}, reuniu-se em Assembleia Geral Ordinária a membresia sob a presidência do(a) {{PRESIDENTE}}, tendo como secretário(a) ad-hoc o(a) {{REDATOR}}.

1. DA ABERTURA E ORAÇÃO INICIAL:
O Presidente declarou aberta a presente assembleia com a leitura da Palavra de Deus em [Texto Bíblico] e oração de louvor e gratidão.

2. DA PAUTA DO DIA:
A reunião foi convocada com a seguinte ordem do dia:
a) Leitura e aprovação da ata anterior;
b) Apresentação do relatório financeiro e balancete da tesouraria;
c) Parecer do Conselho Fiscal;
d) [Assuntos Gerais].

3. DAS DELIBERAÇÕES E DECISÕES:
[Descrever detalhadamente os pontos discutidos e o resultado das votações (aprovado por unanimidade ou maioria)].

4. DO ENCERRAMENTO:
Nada mais havendo a tratar, a presente reunião foi encerrada às {{HORA_TERMINO}} horas com oração final impetrada pelo(a) [Nome]. Eu, {{REDATOR}}, na qualidade de secretário(a), lavrei a presente ata que, após lida e considerada conforme, segue assinada por mim e pela presidência da mesa diretora.

{{CIDADE_UF}}, {{DATA_FORMATADA}}.

___________________________________________
{{PRESIDENTE}}
Presidente da Assembleia

___________________________________________
{{REDATOR}}
Secretário(a)"""

ASSEMBLEIA_EXTRA_BODY = """No dia {{DIA}} de {{MES_EXTENSO}} de {{ANO}}, às {{HORA}} horas, nas dependências da {{NOME_IGREJA}}, situada à {{ENDERECO_IGREJA}}, reuniu-se em Assembleia Geral Extraordinária a membresia, convocada especialmente para tratar do(s) seguinte(s) assunto(s), sob a presidência do(a) {{PRESIDENTE}} e com a secretaria do(a) {{REDATOR}}.

1. DA CONVOCAÇÃO:
A presente assembleia foi convocada nos termos do [Estatuto / Edital de Convocação], tendo por objeto: [Ex.: Eleição da diretoria; Reforma de estatuto; Decisões patrimoniais].

2. DA ABERTURA E ORAÇÃO INICIAL:
O Presidente declarou aberta a sessão com a leitura bíblica em [Texto Bíblico] e oração inicial proferida por [Nome].

3. DAS DELIBERAÇÕES:
a) [Ponto 1]: [Relatar discussões, propostas apresentadas e resultado da votação];
b) [Ponto 2]: [Relatar discussões, propostas apresentadas e resultado da votação];
c) [Ponto 3]: [Relatar discussões, propostas apresentadas e resultado da votação].

4. DO ENCERRAMENTO:
Nada mais havendo a tratar, a sessão foi encerrada às {{HORA_TERMINO}} horas com oração final [Nome]. Eu, {{REDATOR}}, na qualidade de secretário(a), lavrei a presente ata que, após lida e considerada conforme, segue assinada por mim e pelo Presidente da mesa.

{{CIDADE_UF}}, {{DATA_FORMATADA}}.

___________________________________________
{{PRESIDENTE}}
Presidente da Assembleia

___________________________________________
{{REDATOR}}
Secretário(a)"""

MEETING_BODIES = {
    ChurchMinutes.MeetingType.OUTRO: LIDERANCA_BODY,
    ChurchMinutes.MeetingType.DIRETORIA: DIRETORIA_BODY,
    ChurchMinutes.MeetingType.CONSELHO: CONSELHO_BODY,
    ChurchMinutes.MeetingType.ASSEMBLEIA_GERAL: ASSEMBLEIA_GERAL_BODY,
    ChurchMinutes.MeetingType.ASSEMBLEIA_EXTRAORDINARIA: ASSEMBLEIA_EXTRA_BODY,
}

MEETING_LABELS = {
    ChurchMinutes.MeetingType.OUTRO: 'Reunião da Liderança',
    ChurchMinutes.MeetingType.DIRETORIA: 'Reunião da Diretoria',
    ChurchMinutes.MeetingType.CONSELHO: 'Reunião do Conselho Fiscal',
    ChurchMinutes.MeetingType.ASSEMBLEIA_GERAL: 'Assembleia Ordinária',
    ChurchMinutes.MeetingType.ASSEMBLEIA_EXTRAORDINARIA: 'Assembleia Extraordinária',
}


def _name(rng, used):
    for _ in range(200):
        name = f'{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}'
        if name not in used:
            used.add(name)
            return name
    return f'Membro {len(used) + 1}'


def _interpolate(body, meeting_date, church, president, recorder, start, end):
    month_pt = MONTHS[meeting_date.month - 1]
    data_formatada = f'{meeting_date.day} de {month_pt.lower()} de {meeting_date.year}'
    church_addr = ', '.join(
        x for x in (church.street, church.number) if x
    )
    church_addr = ' - '.join(
        x for x in (church_addr, church.neighborhood) if x
    )
    city_uf = f'{church.city}/{church.state}' if church.city else (church.state or '')
    values = {
        'DIA': f'{meeting_date.day:02d}',
        'MES_EXTENSO': month_pt.lower(),
        'ANO': str(meeting_date.year),
        'DATA_FORMATADA': data_formatada,
        'HORA': start,
        'HORA_TERMINO': end,
        'NOME_IGREJA': church.name,
        'ENDERECO_IGREJA': church_addr,
        'CIDADE_UF': city_uf,
        'PRESIDENTE': president,
        'REDATOR': recorder,
        'LOCAL': '',
    }
    out = body
    for key, value in values.items():
        out = out.replace('{{%s}}' % key, value)
    out = re.sub(r'\{\{[A-Z_]+\}\}', '', out)
    return re.sub(r' {2,}', ' ', out).strip()


def _body_to_html(text):
    blocks = []
    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        if re.search(r'_{3,}', line):
            blocks.append(f'<p style="text-align:center">{line}</p>')
        elif re.match(r'^\d+\.\s', line):
            blocks.append(f'<p><strong>{line}</strong></p>')
        else:
            blocks.append(f'<p>{line}</p>')
    return ''.join(blocks)


class Command(BaseCommand):
    help = (
        'Cria dados de demonstração na igreja "Assembleia De Deus - Sede Central" '
        '(cultos, inventário/empréstimos, atas, EBD, orações, visitas, GCs e '
        'certificados). Idempotente: pode ser executado várias vezes.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--church-id', type=int, default=None,
            help='ID da igreja-alvo (padrão: busca por nome).',
        )

    def handle(self, *args, **options):
        self.seed = random.Random(740026)

        if options.get('church_id'):
            church = Church.objects.filter(pk=options['church_id']).first()
            if church is None:
                raise CommandError(f'Igreja com id {options["church_id"]} não encontrada.')
        else:
            church = (
                Church.objects
                .filter(name__startswith='Assembleia De Deus - Sede Central')
                .order_by('id').first()
            )
            if church is None:
                raise CommandError('Igreja "Assembleia De Deus - Sede Central" não encontrada.')

        self.stdout.write(f'Alvo: {church.name} (id={church.id})')

        secretary = (
            User.objects.filter(
                email='secretaria.sede@demo.idb',
                church_memberships__church_id=church.id,
            ).first()
        )
        pastor = (
            User.objects.filter(
                email='pastor.sede@demo.idb',
                church_memberships__church_id=church.id,
            ).first()
        )
        staff = secretary or pastor or User.objects.filter(is_superuser=True).first()

        self.today = timezone.localdate()
        members = list(
            Member.objects.filter(church=church, status='ACTIVE').order_by('id')
        )
        self.member_names = {m.name for m in members}
        self._seed_cultos(church, staff)
        self._seed_inventory(church, staff)
        self._seed_atas(church, staff)
        self._seed_ebd(church, staff)
        self._seed_prayer_requests(church, staff)
        self._seed_visits(church, staff)
        self._seed_growth_groups(church, staff)
        self._seed_certificates(church, staff)
        self.stdout.write(self.style.SUCCESS('Seed concluído.'))

    # ---------------------------------------------------------------- cultos
    def _candidate_service_dates(self):
        """Domingos (09:00/19:00) e semanas (19:30) de 2026 até hoje."""
        slots = []
        base = date(2026, 1, 4)
        while base <= self.today:
            slots.append((base, time(9, 0)))
            slots.append((base, time(19, 0)))
            for off, t in ((3, time(19, 30)), (5, time(19, 30))):
                d = base + timedelta(days=off)
                if d <= self.today:
                    slots.append((d, t))
            base += timedelta(days=7)
        rng = self.seed
        kinds = ['CELEBRACAO', 'CELEBRACAO', 'CELEBRACAO', 'DOUTRINA',
                 'ORACAO', 'VIGILIA', 'CEIA', 'ESCOLA_BIBLICA', 'JOVENS', 'OUTRO']
        out = []
        for d, t in slots:
            out.append({
                'date': d,
                'time': t,
                'service_type': rng.choice(kinds),
                'theme': rng.choice(SERVICE_THEMES),
                'preacher': rng.choice(PREACHERS),
                'presider': rng.choice(PRESIDERS),
                'scripture': rng.choice(
                    ['Romanos 12.2', 'Mateus 6.33', 'Salmos 23.1', 'João 3.16',
                     'Filipenses 4.6', 'Provérbios 3.5', 'Isaías 41.10']
                ),
                'attendees': rng.randint(60, 320),
                'visitors': rng.randint(0, 25),
                'conversions': rng.randint(0, 8),
                'offering': Decimal(str(rng.randint(30, 900) * 10 + rng.randint(0, 9))),
                'notes': '',
            })
        return out

    def _seed_cultos(self, church, staff):
        target = 130
        created = 0
        used = set()
        candidates = self._candidate_service_dates()
        for cand in candidates:
            if WorshipService.objects.filter(church=church).count() >= target:
                break
            if WorshipService.objects.filter(
                church=church,
                date=cand['date'],
                time=cand['time'],
                service_type=cand['service_type'],
            ).exists():
                continue
            key = (cand['date'], cand['time'], cand['service_type'])
            if key in used:
                continue
            used.add(key)
            WorshipService.objects.create(church=church, created_by=staff, **cand)
            created += 1
            if WorshipService.objects.filter(church=church).count() >= target:
                break
        self.stdout.write(
            f'Cultos: criados {created} (total={WorshipService.objects.filter(church=church).count()})'
        )

    # -------------------------------------------------------------- inventário
    def _seed_inventory(self, church, staff):
        locations = list(StorageLocation.objects.filter(church=church).order_by('id'))
        if not locations:
            raise CommandError('A igreja-alvo não possui locais de armazenamento cadastrados.')
        existing = set(MaterialItem.objects.filter(church=church).values_list('name', flat=True))
        pool = [
            ('Notebook Dell Inspiron 15', 'Notebook corporativo da secretaria, i5, 8GB, SSD 256GB', locations[0]),
            ('Projetor Multimídia Epson', 'Projetor Full HD 3600 lúmens para o templo', locations[1]),
            ('Caixa de Som PA 15"', 'Caixa amplificada 1000W com suporte', locations[1]),
            ('Kit Conga / Aspirador', 'Aspirador de pó para limpeza do templo', locations[3]),
            ('Bandeja de Santa Ceia', 'Bandeja em inox com cálices', locations[0]),
        ]
        created = 0
        for name, desc, loc in pool:
            if name in existing:
                continue
            MaterialItem.objects.create(church=church, name=name,
                                        description=desc, location=loc)
            created += 1
        self.stdout.write(
            f'Materiais: criados {created} (total={MaterialItem.objects.filter(church=church).count()})'
        )
        self._seed_loans(church, staff)

    def _seed_loans(self, church, staff):
        # Realinha exatamente ao conjunto demo (12 devolvidos / 12 ativos /
        # 6 em atraso = 30), determinístico e idempotente.
        Loan.objects.filter(church=church).delete()
        items = list(MaterialItem.objects.filter(church=church).order_by('id'))
        members = list(
            Member.objects.filter(church=church).order_by('name')[:30]
        )
        rng = random.Random(740031)
        plans = {
            'returned': [
                date(2026, 1, 12), date(2026, 2, 3), date(2026, 2, 20),
                date(2026, 3, 10), date(2026, 4, 6), date(2026, 5, 4),
                date(2026, 5, 25), date(2026, 6, 8), date(2026, 7, 6),
                date(2026, 7, 20), date(2026, 8, 11), date(2026, 8, 17),
            ],
            'active': [
                date(2026, 8, 3), date(2026, 8, 12), date(2026, 8, 18),
                date(2026, 8, 25), date(2026, 9, 1), date(2026, 9, 2),
                date(2026, 9, 8), date(2026, 9, 10), date(2026, 9, 15),
                date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 24),
            ],
            'overdue': [
                date(2026, 6, 1), date(2026, 6, 15), date(2026, 7, 1),
                date(2026, 7, 13), date(2026, 8, 1), date(2026, 8, 10),
            ],
        }
        created = 0
        open_used = set()
        for status, dates in plans.items():
            for j, borrowed_at in enumerate(dates):
                idx = (borrowed_at.toordinal() + j) % len(items)
                item = items[idx]
                if status in ('active', 'overdue'):
                    if item.id in open_used:
                        for step in range(1, len(items)):
                            candidate = items[(idx + step) % len(items)]
                            if candidate.id not in open_used:
                                item = candidate
                                break
                        else:
                            continue
                    open_used.add(item.id)
                    expected = borrowed_at + timedelta(days=30)
                    if status == 'overdue':
                        expected = min(expected, self.today - timedelta(days=3))
                    else:
                        expected = max(expected, self.today + timedelta(days=5))
                    loan = Loan.objects.create(
                        church=church, item=item, borrowed_at=borrowed_at,
                        expected_return=expected, created_by=staff,
                        notes='Uso em atividade da igreja conforme carta de empréstimo.',
                    )
                else:
                    loan = Loan.objects.create(
                        church=church, item=item, borrowed_at=borrowed_at,
                        expected_return=borrowed_at + timedelta(days=30),
                        created_by=staff,
                        returned_at=timezone.make_aware(
                            datetime.combine(borrowed_at + timedelta(days=10), time(18, 0))
                        ),
                        returned_by=staff,
                        notes='Devolvido em perfeitas condições.',
                    )
                m = members[idx % len(members)] if members else None
                if m:
                    loan.member = m
                else:
                    loan.borrower_name = f'{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}'
                loan.borrower_phone = f'(83) 9{rng.randint(8000, 9999)}-{rng.randint(1000, 9999)}'
                loan.save()
                created += 1
        self.stdout.write(
            f'Empréstimos: criados {created} (total={Loan.objects.filter(church=church).count()})'
        )

    # ------------------------------------------------------------------- atas
    def _meeting_date_pool(self, day_pattern=(10, 20)):
        dates = []
        for m in range(1, 13):
            for d in day_pattern:
                if d > calendar.monthrange(2026, m)[1]:
                    continue
                candidate = date(2026, m, d)
                if candidate > self.today:
                    continue
                dates.append(candidate)
        return dates

    def _seed_atas(self, church, staff):
        president = 'Carlos Alberto dos Santos'
        recorder = 'Adriana de Fátima Nunes'
        # Totais-alvo por tipo (a igreja já possui 2 AG, 1 AE e 1 Diretoria).
        counts = {
            ChurchMinutes.MeetingType.OUTRO: 12,
            ChurchMinutes.MeetingType.DIRETORIA: 8,
            ChurchMinutes.MeetingType.CONSELHO: 12,
            ChurchMinutes.MeetingType.ASSEMBLEIA_GERAL: 2,
            ChurchMinutes.MeetingType.ASSEMBLEIA_EXTRAORDINARIA: 3,
        }
        day_patterns = {
            ChurchMinutes.MeetingType.OUTRO: (5, 15, 25),
            ChurchMinutes.MeetingType.DIRETORIA: (5, 10, 15, 20),
            ChurchMinutes.MeetingType.CONSELHO: (5, 10, 20, 28),
            ChurchMinutes.MeetingType.ASSEMBLEIA_GERAL: (5, 10, 20),
            ChurchMinutes.MeetingType.ASSEMBLEIA_EXTRAORDINARIA: (5, 10, 20, 28),
        }
        created = 0
        for meeting_type, target_total in counts.items():
            existing = set(
                ChurchMinutes.objects.filter(
                    church=church, meeting_type=meeting_type,
                ).values_list('meeting_date', flat=True)
            )
            desired_new = target_total - len(existing)
            if desired_new <= 0:
                continue
            body = MEETING_BODIES[meeting_type]
            label = MEETING_LABELS[meeting_type]
            created_this_type = 0
            pool = self._meeting_date_pool(day_patterns[meeting_type])
            for d in pool:
                if created_this_type >= desired_new:
                    break
                if d in existing:
                    continue
                start, end = '19:30', '21:00'
                content = _body_to_html(_interpolate(
                    body, d, church, president, recorder, start, end,
                ))
                ChurchMinutes.objects.create(
                    church=church,
                    title=f'{label} - {d.strftime("%d/%m/%Y")}',
                    meeting_type=meeting_type,
                    meeting_date=d,
                    location='Templo Central',
                    recorder=recorder,
                    participants='Mesa diretora e participantes presentes conforme lista de presença.',
                    content=content,
                    created_by=staff,
                )
                existing.add(d)
                created += 1
                created_this_type += 1
        self.stdout.write(
            f'Atas: criadas {created} (total={ChurchMinutes.objects.filter(church=church).count()})'
        )

    # -------------------------------------------------------------------- EBD
    def _sundays_until_today(self):
        sundays = []
        start = date(2026, 1, 4)
        d = start
        while d <= self.today:
            sundays.append(d)
            d += timedelta(days=7)
        return sundays

    def _seed_ebd(self, church, staff):
        classes = list(
            SundaySchoolClass.objects.filter(church=church, is_active=True).order_by('id')
        )
        if not classes:
            self.stdout.write(self.style.WARNING('EBD: nenhuma classe ativa encontrada.'))
            return
        per_class = 52
        rng = random.Random(740052)
        for cls in classes:
            existing_names = set(
                cls.enrollments.values_list('student_name', flat=True)
            )
            needed = per_class - cls.enrollments.count()
            to_create = []
            while len(to_create) < needed:
                stitch = {c.student_name for c in to_create}
                name = _name(rng, existing_names | stitch)
                to_create.append(SundaySchoolEnrollment(
                    sunday_school_class=cls,
                    student_name=name,
                    phone=f'(83) 9{rng.randint(8000, 9999)}-{rng.randint(1000, 9999)}',
                    is_active=True,
                ))
            if to_create:
                SundaySchoolEnrollment.objects.bulk_create(to_create, batch_size=500)
            self.stdout.write(
                f'EBD [{cls.name}]: {cls.enrollments.count()} alunos matriculados'
            )

        sessions = 0
        attendance_rows = 0
        for cls in classes:
            enrollments = list(cls.enrollments.filter(is_active=True))
            for d in self._sundays_until_today():
                session, _ = SundaySchoolSession.objects.update_or_create(
                    sunday_school_class=cls,
                    date=d,
                    defaults={
                        'topic': rng.choice(SERVICE_THEMES),
                        'bibles_count': rng.randint(40, 52),
                        'magazines_count': rng.randint(30, 50),
                        'visitors_count': rng.randint(0, 8),
                        'offering_amount': Decimal(str(rng.randint(20, 160))),
                        'notes': 'Aula expositiva com participação da turma.',
                        'registered_by': staff,
                    },
                )
                sessions += 1
                rows = []
                for enrollment in enrollments:
                    prng = random.Random(session.pk * 100000 + enrollment.pk)
                    present = prng.random() < 0.80
                    rows.append(SundaySchoolAttendance(
                        session=session,
                        enrollment=enrollment,
                        is_present=present,
                        brought_bible=present and prng.random() < 0.75,
                        brought_magazine=present and prng.random() < 0.65,
                    ))
                SundaySchoolAttendance.objects.bulk_create(
                    rows, batch_size=500, ignore_conflicts=True,
                )
                attendance_rows += len(rows)
        self.stdout.write(
            f'EBD: {sessions} sessões e {attendance_rows} presenças registradas'
        )

    # -------------------------------------------------------- pedidos de oração
    def _seed_prayer_requests(self, church, staff):
        statuses = (
            ['PENDING'] * 18 + ['PRAYING'] * 14
            + ['VISIT_SCHEDULED'] * 5 + ['ANSWERED'] * 8 + ['ARCHIVED'] * 5
        )
        categories = (
            ['HEALTH', 'FAMILY', 'SPIRITUAL', 'FINANCIAL', 'GRIEF',
             'THANKSGIVING', 'OTHER'] * 8
        )
        periods = ['MORNING', 'AFTERNOON', 'NIGHT', 'ANY']
        created = 0
        # rng dedicado por registro => geração determinística e idempotente.
        for i in range(len(statuses)):
            r = random.Random(7400500 + i)
            desc = r.choice(PRAYER_DESCRIPTIONS)
            name = _name(r, set())
            obj, was_created = PrayerRequest.objects.get_or_create(
                church=church,
                requester_name=name,
                description=desc,
                defaults={
                    'requester_phone': f'(83) 9{r.randint(8000, 9999)}-{r.randint(1000, 9999)}',
                    'is_anonymous': r.random() < 0.2,
                    'category': categories[i % len(categories)],
                    'wants_visit': r.random() < 0.7,
                    'cep': '58400-000',
                    'street': 'Rua ' + r.choice(FIRST_NAMES),
                    'number': str(r.randint(1, 900)),
                    'neighborhood': r.choice(BARRIS_CAMPINA),
                    'city': 'Campina Grande',
                    'state': 'PB',
                    'preferred_period': r.choice(periods),
                    'status': statuses[i],
                },
            )
            created += 1 if was_created else 0
        self.stdout.write(
            f'Pedidos de oração: criados {created} (total={PrayerRequest.objects.filter(church=church).count()})'
        )

    # ----------------------------------------------------------- visitas pastorais
    def _seed_visits(self, church, staff):
        plan = [(2026, 7, 50), (2026, 8, 30), (2026, 9, 20)]
        visit_types = ['ROUTINE', 'ILLNESS', 'BEREAVEMENT', 'NEW_CONVERT',
                       'SOCIAL_AID', 'SPECIAL']
        created = 0
        idx = 0
        for year, month, count in plan:
            last_day = calendar.monthrange(year, month)[1]
            for i in range(count):
                day = (i % last_day) + 1
                scheduled = date(year, month, day)
                if scheduled > self.today:
                    scheduled = self.today
                r = random.Random(740007 + idx)
                idx += 1
                name = _name(r, set())
                status = r.choice(['COMPLETED', 'COMPLETED', 'COMPLETED',
                                   'PLANNED', 'CANCELLED'])
                completed_at = None
                if status == 'COMPLETED':
                    completed_at = timezone.make_aware(
                        datetime.combine(scheduled, time(17, 0))
                    )
                if status == 'PLANNED' and scheduled > self.today:
                    status = 'COMPLETED'
                    completed_at = timezone.make_aware(
                        datetime.combine(scheduled, time(17, 0))
                    )
                _, was_created = PastoralVisit.objects.get_or_create(
                    church=church,
                    target_name=name,
                    scheduled_date=scheduled,
                    competence_year=year,
                    competence_month=month,
                    defaults={
                        'member': None,
                        'target_phone': f'(83) 9{r.randint(8000, 9999)}-{r.randint(1000, 9999)}',
                        'visit_type': r.choice(visit_types),
                        'status': status,
                        'completed_at': completed_at,
                        'visited_by': r.choice([
                            'Carlos Alberto dos Santos', 'Diác. João Barbosa',
                            'Ev. Paula Almeida', 'Diác. Pedro Pereira',
                        ]),
                        'notes': 'Visita com leitura bíblica, oração e escuta qualificada.',
                        'needs_followup': r.random() < 0.3,
                        'cep': '58400-000',
                        'street': 'Rua ' + r.choice(FIRST_NAMES),
                        'number': str(r.randint(1, 900)),
                        'neighborhood': r.choice(BARRIS_CAMPINA),
                        'city': 'Campina Grande',
                        'state': 'PB',
                        'created_by': staff,
                    },
                )
                created += 1 if was_created else 0
        self.stdout.write(
            f'Visitas: criadas {created} (total={PastoralVisit.objects.filter(church=church).count()})'
        )

    # ------------------------------------------------------- grupos de crescimento
    def _seed_growth_groups(self, church, staff):
        members = list(
            Member.objects.filter(church=church).order_by('id')[:20]
        )
        names = [
            'Gedeão', 'Ebenézer', 'Milagre', 'Bethânia', 'Sião', 'Efraim',
            'Jeruel', 'Rute', 'Salem', 'Canaã', 'BeteLuz', 'Manancial',
            'Filhos da Promessa', 'Avivamento', 'Adonai',
        ]
        categories = ['ADULTS', 'YOUTH', 'TEENS', 'WOMEN', 'MEN', 'MIXED']
        weekdays = [0, 1, 2, 4]
        rng = random.Random(740015)
        created = 0
        used_names = set(
            GrowthGroup.objects.filter(church=church).values_list('name', flat=True)
        )
        for name in names:
            if name in used_names:
                continue
            if not members:
                break
            leader = members[created % len(members)]
            host = members[(created + 1) % len(members)]
            weekday = weekdays[created % len(weekdays)]
            GrowthGroup.objects.create(
                church=church,
                name=name,
                leader=leader,
                host=host,
                weekday=weekday,
                time=time(19, 30),
                cep='58400-000',
                street='Rua ' + rng.choice(FIRST_NAMES),
                number=str(rng.randint(1, 999)),
                neighborhood=rng.choice(BARRIS_CAMPINA),
                city='Campina Grande',
                state='PB',
                radius_meters=rng.choice([500, 1000, 1500]),
                category=rng.choice(categories),
                is_active=True,
                created_by=staff,
            )
            created += 1
        self.stdout.write(
            f'Grupos de crescimento: criados {created} (total={GrowthGroup.objects.filter(church=church).count()})'
        )

    # ----------------------------------------------------------- certificados
    def _seed_certificates(self, church, staff):
        templates = {
            t.certificate_type: t
            for t in CertificateTemplate.objects.filter(church=church, is_active=True)
        }
        distribution = (
            ['BAPTISM'] * 18 + ['MEMBERSHIP_COURSE'] * 12
            + ['CHILD_PRESENTATION'] * 7 + ['CUSTOM'] * 5
        )
        existing = set(
            EcclesiasticalCertificate.objects.filter(church=church)
            .values_list('recipient_name', 'event_date', 'certificate_type')
        )
        created = 0
        for i, ctype in enumerate(distribution):
            r = random.Random(7400450 + i)
            name = _name(r, set())
            event_date = date(2025, r.randint(1, 12), r.randint(1, 28))
            if (name, event_date, ctype) in existing:
                continue
            template = templates.get(ctype)
            EcclesiasticalCertificate.objects.create(
                church=church,
                template=template,
                certificate_type=ctype,
                member=None,
                recipient_name=name,
                father_name=_name(random.Random(7400450 + i + 1000), set()),
                mother_name=_name(random.Random(7400450 + i + 2000), set()),
                event_date=event_date,
                officiant_name=r.choice([
                    'Pr. Carlos Alberto dos Santos', 'Pr. Antônio Souza',
                ]),
                scripture_verse=r.choice([
                    'Mateus 3:16', 'Gênesis 12:2', 'Atos 2:38', 'Provérbios 22:6',
                ]),
                registry_book=str(r.randint(1, 50)),
                registry_page=str(r.randint(1, 300)),
                registry_number=f'2026-{r.randint(1, 5000):04d}',
                created_by=staff,
            )
            created += 1
        self.stdout.write(
            f'Certificados: criados {created} (total={EcclesiasticalCertificate.objects.filter(church=church).count()})'
        )