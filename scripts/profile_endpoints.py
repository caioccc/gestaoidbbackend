"""Benchmark read-only dos endpoints GET declarados no resolver Django.

Execute a partir de ``backend`` com o ambiente ``tutoriando``:
``conda run -n tutoriando python scripts/profile_endpoints.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

import django


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.db import connection, reset_queries  # noqa: E402
from django.apps import apps  # noqa: E402
from django.urls import URLPattern, URLResolver, get_resolver  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from accounts.models import Church, Member, User  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
PARAM_RE = re.compile(r"<(?:(?P<converter>[^:>]+):)?(?P<name>[^>]+)>")


def _first_id(model: Any) -> str | None:
    row = model.objects.order_by("pk").first()
    return str(row.pk) if row else None


def _parameter_value(name: str, converter: str | None, route: str) -> str | None:
    if converter in {"uuid", "slug"} or name in {"hash", "slug"}:
        return None
    if name == "church_pk":
        church_scoped_models = {
            "entries": ("finance", "FinancialEntry"),
            "exits": ("finance", "FinancialExit"),
            "receipts": ("finance", "FinancialReceipt"),
            "tithers": ("finance", "Tither"),
            "events": ("finance", "CalendarEvent"),
            "members": ("accounts", "Member"),
            "growth-groups": ("accounts", "GrowthGroup"),
        }
        for segment, model_name in church_scoped_models.items():
            if f"/{segment}/" in route:
                row = apps.get_model(*model_name).objects.order_by("pk").first()
                if row is not None:
                    return str(row.church_id)
    model_by_name = {
        "church_pk": Church,
        "church_id": Church,
        "church": Church,
        "member_pk": Member,
        "member_id": Member,
        "tither_pk": apps.get_model("finance", "Tither"),
    }
    model = model_by_name.get(name)
    if model is None and name == "pk":
        segment_models = {
            "churches": ("accounts", "Church"),
            "members": ("accounts", "Member"),
            "ministry-areas": ("accounts", "MinistryArea"),
            "entries": ("finance", "FinancialEntry"),
            "exits": ("finance", "FinancialExit"),
            "tithers": ("finance", "Tither"),
            "receipts": ("finance", "FinancialReceipt"),
            "events": ("finance", "CalendarEvent"),
            "songs": ("music", "Song"),
            "setlists": ("music", "BandSetlist"),
            "bands": ("music", "Band"),
            "growth-groups": ("accounts", "GrowthGroup"),
            "pastoral-visits": ("accounts", "PastoralVisit"),
            "prayer-requests": ("accounts", "PrayerRequest"),
        }
        preceding_segment = re.split(r"<(?:int:)?pk>", route, maxsplit=1)[0].rstrip("/").split("/")[-1]
        model_name = segment_models.get(preceding_segment)
        if model_name:
            model = apps.get_model(*model_name)
    model = model or Member
    if name == "pk" and "/churches/<int:church_pk>/members/" in route and model is Member:
        church_id = _first_id(Church)
        member_id = model.objects.filter(church_id=church_id).order_by("pk").values_list("pk", flat=True).first()
        return str(member_id or _first_id(model)) if church_id else _first_id(model)
    value = _first_id(model)
    if value is None and converter == "int":
        return "1"
    return value


def _route_path(pattern: Any, prefix: str = "") -> str:
    route = getattr(pattern, "_route", None)
    if route is None:
        route = getattr(getattr(pattern, "regex", None), "pattern", str(pattern))
        route = route.replace("^", "").replace("$", "")
        route = re.sub(r"\\?\.\(\?P<format>[^)]+\)(?:/)?\??", "", route)
        route = re.sub(r"\(\?P<([^>]+)>[^)]+\)", r"<\1>", route)
        route = route.replace("\\.", ".").replace("?", "")
    return f"{prefix}{route}"


def _collect_patterns(urlpatterns: list[Any], prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    for pattern in urlpatterns:
        if isinstance(pattern, URLResolver):
            child_prefix = _route_path(pattern.pattern, prefix)
            found.extend(_collect_patterns(pattern.url_patterns, child_prefix))
        elif isinstance(pattern, URLPattern):
            found.append((_route_path(pattern.pattern, prefix), pattern))
    return found


def _materialize(path: str) -> str | None:
    def replace(match: re.Match[str]) -> str:
        value = _parameter_value(match.group("name"), match.group("converter"), path)
        if value is None:
            raise ValueError(match.group(0))
        return value

    try:
        path = PARAM_RE.sub(replace, path)
    except ValueError:
        return None
    path = path.replace("//", "/")
    if not path.startswith("/"):
        path = "/" + path
    if path.endswith("/"):
        return path
    return path + "/"


def _query_for(path: str) -> str:
    if "dashboard/summary" in path or "summary/" in path:
        return "?year=2026"
    if "setlists/" in path:
        return "?month=2026-09"
    if "calendar/events" in path:
        return "?start_date=2026-09-01&end_date=2026-09-30"
    if "validation/" in path or "reconciliation/" in path:
        return "?year=2026&month=9"
    return ""


def _payload_size_kb(response: Any) -> float | None:
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower():
        return None
    return round(len(response.content) / 1024, 3)


def _supports_get(pattern: URLPattern) -> bool:
    callback = pattern.callback
    actions = getattr(callback, "actions", None)
    if actions is not None:
        return "get" in actions
    view_class = getattr(callback, "view_class", None)
    if view_class is not None:
        return callable(getattr(view_class, "get", None))
    return callable(getattr(callback, "get", None)) or getattr(callback, "__name__", "") == "healthz"


def benchmark(client: APIClient, path: str) -> dict[str, Any]:
    timings: list[float] = []
    query_counts: list[int] = []
    status_codes: list[int] = []
    payload_kb: float | None = None
    error: str | None = None

    for _ in range(3):
        reset_queries()
        started = time.perf_counter()
        try:
            response = client.get(path, follow=True)
            if hasattr(response, "render"):
                response.render()
            status_codes.append(response.status_code)
            payload_kb = _payload_size_kb(response)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            status_codes.append(599)
        timings.append((time.perf_counter() - started) * 1000)
        query_counts.append(len(connection.queries))

    return {
        "method": "GET",
        "endpoint": path,
        "average_ms": round(mean(timings), 3),
        "queries": max(query_counts),
        "query_counts": query_counts,
        "payload_kb": payload_kb,
        "status": status_codes[-1],
        "status_codes": status_codes,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-email", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", default=str(ROOT / "profile_endpoints.json"))
    args = parser.parse_args()

    user_qs = User.objects.filter(is_active=True, is_staff=True)
    user = (
        user_qs.filter(email=args.user_email).first()
        if args.user_email
        else user_qs.filter(church__isnull=False).first() or user_qs.first()
    )
    if user is None:
        raise SystemExit("Nenhum usuário staff ativo encontrado para autenticação.")

    client = APIClient()
    client.force_authenticate(user=user)
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for raw_path, pattern in _collect_patterns(get_resolver().url_patterns):
        if not (raw_path.startswith("api/") or raw_path == "healthz/"):
            continue
        if not _supports_get(pattern):
            continue
        path = _materialize(raw_path)
        if path is None:
            skipped.append({"declared_path": raw_path, "reason": "parâmetro sem amostra local"})
            continue
        path += _query_for(path)
        if path in seen:
            continue
        seen.add(path)
        entries.append(benchmark(client, path))
        if args.limit and len(entries) >= args.limit:
            break

    result = {
        "environment": "tutoriando",
        "database": str(settings.DATABASES["default"].get("NAME", "")),
        "user_email": user.email,
        "runs_per_endpoint": 3,
        "endpoints": entries,
        "skipped": skipped,
    }
    output = Path(args.json)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"benchmarked": len(entries), "skipped": len(skipped), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()