"""Worker local (Docker) que extrai cifras/BPM/tom do Chordify em background.

Motivação: o Cloudflare bloqueia o IP de datacenter da Heroku (WAF/Turnstile)
nas requisições do Selenium. Este worker roda na máquina local do usuário
(IP residencial), polla o banco de PRODUÇÃO via ``TARGET_DATABASE_URL`` e
processa as músicas da fila (``chord_status=PENDING``).

Uso:
    # Uma única passada (útil para teste): processa até --limit músicas e sai.
    python manage.py process_chordify_queue --once --limit 3

    # Loop contínuo (Docker worker):
    python manage.py process_chordify_queue --poll-interval 30 --limit 3

Flags:
    --poll-interval : segundos de espera entre ciclos (default 30)
    --limit         : quantas músicas processar por ciclo (default 3)
    --once          : processa um único ciclo e encerra
    --instrument    : instrumento dos diagramas (guitar/piano; default guitar)
    --max-retries   : tentativas antes de marcar FAILED (default 3)

Convenções:
    - NUNCA sobrescreve ``church_key`` (Tom Definido da igreja).
    - ``original_key``/``bpm`` só são preenchidos se estiverem vazios.
    - Sucesso  -> COMPLETED + chord_processed_at.
    - Falha    -> incrementa chord_retries; em >= max-retries vira FAILED
                   com chord_error preenchido; senão volta a PENDING.
    - Consegue parar limpo com SIGINT/SIGTERM (docker stop).
"""

import logging
import os
import signal
import time
from typing import Any

from django.core.management.base import BaseCommand
from django.db import connection
from django.db import transaction
from django.utils import timezone

from music.models import Song
from music.services import chordify as chordify_service

logger = logging.getLogger('music.worker.chordify')


class Command(BaseCommand):
    help = (
        'Worker local de extração de cifras: processa músicas PENDING '
        'coletando acordes/BPM/tom do Chordify (Selenium) em segundo plano.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--poll-interval', type=float, default=30.0,
            help='Segundos de espera entre ciclos (default: 30).',
        )
        parser.add_argument(
            '--limit', type=int, default=3,
            help='Número máximo de músicas processadas por ciclo (default: 3).',
        )
        parser.add_argument(
            '--once', action='store_true',
            help='Executa um único ciclo e encerra (não fica em loop).',
        )
        parser.add_argument(
            '--instrument', default='guitar',
            help='Instrumento dos diagramas de acorde (guitar ou piano; default: guitar).',
        )
        parser.add_argument(
            '--max-retries', type=int, default=3,
            help='Tentativas antes de marcar a música como FAILED (default: 3).',
        )

    def handle(self, *args, **opts):  # noqa: ARG002
        self.instrument = (opts['instrument'] or 'guitar').lower()
        self.max_retries = max(1, int(opts['max_retries']))
        self.limit = max(1, int(opts['limit']))
        self.interval = max(float(opts['poll_interval']), 1.0)
        self.once = bool(opts['once'])
        self._stop = False

        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        db_vendor = connection.vendor
        db_name = connection.settings_dict.get('NAME', '?')
        db_host = connection.settings_dict.get('HOST', '?')
        logger.info(
            'Worker[inicio] process_chordify_queue iniciado '
            '| instrument=%s max_retries=%d limit=%d poll_interval=%.1fs once=%s '
            '| banco: vendor=%s host=%s db=%s | TARGET_DATABASE_URL definida: %s.',
            self.instrument, self.max_retries, self.limit, self.interval, self.once,
            db_vendor, db_host, db_name,
            'sim' if os.environ.get('TARGET_DATABASE_URL') else 'não (usando DATABASE_URL/local)',
        )

        # Garante conectividade com o banco antes de entrar no ciclo.
        try:
            total_pending = Song.objects.filter(
                chord_status=Song.ChordStatus.PENDING,
            ).exclude(youtube_id='').count()
            logger.info(
                'Worker[db] conexão OK. Músicas na fila (PENDING com youtube_id): %d.',
                total_pending,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                'Worker[db] falha ao consultar o banco alvo (%s): %s. Abortando.',
                db_host,
                exc,
            )
            return

        while not self._stop:
            try:
                processed = self._process_batch()
            except Exception as exc:  # noqa: BLE001
                processed = 0
                logger.exception(
                    'Worker[ciclo] erro inesperado no ciclo: %s', exc,
                )

            if self.once or self._stop:
                break

            remaining = Song.objects.filter(
                chord_status=Song.ChordStatus.PENDING,
            ).exclude(youtube_id='').count()
            logger.info(
                'Worker[ocioso] ciclo processou %d música(s); %d ainda na fila. '
                'Próximo ciclo em %.1fs (Ctrl+C para encerrar).',
                processed, remaining or 0, self.interval,
            )
            try:
                time.sleep(self.interval)
            except KeyboardInterrupt:
                logger.info('Worker[encerrado] interrompido por Ctrl+C.')
                break

        logger.info('Worker[fim] encerrado com sucesso.')

    def _on_signal(self, signum, _frame):
        self._stop = True
        logger.info(
            'Worker[sinal] recebido sinal %s; encerrando após o ciclo atual.',
            signum,
        )

    def _process_batch(self) -> int:
        pending = list(
            Song.objects.filter(
                chord_status=Song.ChordStatus.PENDING,
            )
            .exclude(youtube_id='')
            .order_by('created_at', 'id')
            .select_related('band')[: self.limit]
        )
        if not pending:
            logger.info('Worker[fila] nenhuma música PENDING com youtube_id.')
            return 0

        logger.info(
            'Worker[fila] %d música(s) a processar neste ciclo: %s.',
            len(pending),
            ', '.join(f'#{s.pk} "{s.title}"' for s in pending),
        )
        for song in pending:
            if self._stop:
                break
            self._process_song(song)
        return len(pending)

    def _process_song(self, song: Song) -> None:
        started = time.monotonic()
        logger.info(
            'Worker[música] >>> início: #%s "%s" (youtube_id=%s, retries=%d)',
            song.pk, song.title, song.youtube_id or '-', song.chord_retries,
        )

        song.chord_status = Song.ChordStatus.PROCESSING
        song.save(update_fields=['chord_status', 'updated_at'])
        logger.debug('Worker[música] #%s marcada como PROCESSING.', song.pk)

        try:
            with transaction.atomic():
                result = chordify_service.enrich_chordify_data(
                    song.youtube_id, self.instrument,
                )
                elapsed = time.monotonic() - started
                if result.get('status') != 'success':
                    raise WorkerExtractionError(
                        result.get('reason')
                        or result.get('message')
                        or f"status={result.get('status') or 'unavailable'}"
                    )
                self._complete_song(song, result, elapsed)
        except WorkerExtractionError as exc:
            self._fail_song(song, str(exc), started)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                'Worker[música] #%s exceção inesperada na extração: %s',
                song.pk, exc,
            )
            self._fail_song(song, f'Erro inesperado: {exc}', started)

    def _complete_song(self, song: Song, result: dict[str, Any], elapsed: float) -> None:
        chords_formatada = result.get('chords_formatada') or []
        if not chords_formatada:
            raise WorkerExtractionError(
                'Extração concluída, mas nenhum acorde foi detectado.'
            )

        raw_chords = result.get('chords')
        if not isinstance(raw_chords, str):
            raw_chords = ''
        format_key = (result.get('format_key') or '').strip()
        derived_bpm = result.get('derivedBpm')

        updates: dict[str, Any] = {
            'chords': raw_chords,
            'chords_json': chords_formatada,
            'chord_status': Song.ChordStatus.COMPLETED,
            'chord_error': '',
            'chord_retries': 0,
            'chord_processed_at': timezone.now(),
        }

        # Regra de ouro: TOM DEFINIDO (church_key) NUNCA é sobrescrito.
        if not song.original_key and format_key:
            updates['original_key'] = format_key
        if song.bpm is None and derived_bpm:
            try:
                updates['bpm'] = int(round(float(derived_bpm)))
            except (TypeError, ValueError):
                updates['bpm'] = None

        for field, value in updates.items():
            setattr(song, field, value)
        song.save(update_fields=list(updates.keys()) + ['updated_at'])

        logger.info(
            'Worker[sucesso] >>> #%s "%s" COMPLETO em %.2fs '
            '| origem=%s acordes=%d tom_original=%s bpm=%s '
            '| church_key preservado=%s original_key_preenchido=%s bpm_preenchido=%s',
            song.pk, song.title, elapsed,
            result.get('source') or 'next_data',
            len(chords_formatada),
            format_key or '-',
            song.bpm if updates.get('bpm') else '-',
            song.church_key or '-',
            bool(updates.get('original_key')),
            updates.get('bpm') is not None,
        )

    def _fail_song(self, song: Song, reason: str, started: float) -> None:
        elapsed = time.monotonic() - started
        next_retries = min(song.chord_retries + 1, 999)
        failed = next_retries >= self.max_retries
        status = Song.ChordStatus.FAILED if failed else Song.ChordStatus.PENDING

        song.chord_status = status
        song.chord_error = f'{reason}'[:2000]
        song.chord_retries = next_retries
        song.chord_processed_at = timezone.now()
        song.save(update_fields=[
            'chord_status', 'chord_error', 'chord_retries', 'chord_processed_at',
            'updated_at',
        ])

        if failed:
            logger.warning(
                'Worker[falha] >>> #%s "%s" em %.2fs atingiu %d tentativas '
                'e foi marcada como FAILED | motivo: %s',
                song.pk, song.title, elapsed, next_retries, reason,
            )
        else:
            logger.warning(
                'Worker[falha] >>> #%s "%s" em %.2fs: tentativa %d de %d. '
                'Voltando para PENDING (rerun) | motivo: %s',
                song.pk, song.title, elapsed, next_retries, self.max_retries, reason,
            )


class WorkerExtractionError(Exception):
    """Falha esperada durante a extração (o serviço já retornou unavailable)."""