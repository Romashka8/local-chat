from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


from local_agent_chat.artifacts import ArtifactStore

from . import engine
from .demo_data import build_demo_data


STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_simulation_state (
    chat_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(frozen=True, slots=True)
class GreenplumConfig:
    host: str | None
    port: int
    database: str | None
    user: str | None
    password: str | None


class CollectionSimulationService:
    """Application service for the collection communication simulator.

    The LLM only sees narrow tools. Database access, validation, persistence and
    artifact creation stay here. `demo` mode uses the deterministic fixture from
    the colleague prototype; `greenplum` reads the same tables from a real DB.
    """

    def __init__(
        self,
        artifacts: ArtifactStore,
        state_database: Path,
        *,
        mode: str = "demo",
        greenplum: GreenplumConfig | None = None,
    ) -> None:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"demo", "greenplum"}:
            raise ValueError("COLLECTION_SIM_MODE must be 'demo' or 'greenplum'")
        self._artifacts = artifacts
        self._state_database = state_database
        self._mode = normalized_mode
        self._greenplum = greenplum
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._chat_locks: dict[str, asyncio.Lock] = {}

    @property
    def mode(self) -> str:
        return self._mode

    async def portfolio_summary(self) -> dict[str, Any]:
        data = await self._load_data()
        self._validate_data(data)
        start = min(row["as_of_date"] for row in data["scores"])
        policy = data["policy"][0]
        scores = {row["client_id"]: row for row in data["scores"] if row["as_of_date"] == start}
        loans_by_client: dict[int, list[dict]] = defaultdict(list)
        for loan in data["loans"]:
            if loan["as_of_date"] == start and loan["is_active"]:
                loans_by_client[loan["client_id"]].append(loan)

        counts: Counter[str] = Counter()
        for client in data["clients"]:
            cid = client["client_id"]
            score = scores[cid]
            overdue = [
                loan for loan in loans_by_client[cid]
                if loan["dpd"] > 0
            ]
            route_code = engine.route(
                client,
                overdue,
                float(score["self_cure_score"]),
                float(score["repayment_score"]),
                policy,
            )
            counts[route_code] += 1

        return {
            "mode": self._mode,
            "snapshot_date": str(start),
            "clients": sum(counts.values()),
            "by_route": dict(sorted(counts.items())),
        }

    async def simulate_strategy(
        self,
        *,
        chat_id: str,
        days: int,
        intensity: str | None = None,
        text_only: bool = False,
        route: str | None = None,
        weekly_contacts: int | None = None,
        min_calls: int = 0,
    ) -> dict[str, Any]:
        override = self._validate_scenario(
            days=days,
            intensity=intensity,
            text_only=text_only,
            route=route,
            weekly_contacts=weekly_contacts,
            min_calls=min_calls,
        )
        async with self._chat_locks.setdefault(chat_id, asyncio.Lock()):
            data = await self._load_data()
            self._validate_data(data)
            start = min(row["as_of_date"] for row in data["scores"])
            rows, schemes, promise = await asyncio.to_thread(
                engine.simulate,
                data,
                start,
                days,
                [override] if override is not None else [],
                text_only,
            )

            html = await asyncio.to_thread(
                engine.render_report,
                rows,
                schemes,
                promise,
                start,
                days,
            )
            csv_text = _rows_to_csv(rows)
            html_artifact = await self._artifacts.write_text(
                chat_id,
                name=f"collection_simulation_{days}d.html",
                content=html,
            )
            csv_artifact = await self._artifacts.write_text(
                chat_id,
                name=f"collection_simulation_{days}d.csv",
                content=csv_text,
            )

            serializable_rows = [_serialize_row(row) for row in rows]
            state = {
                "mode": self._mode,
                "start_date": str(start),
                "days": days,
                "intensity": override,
                "text_only": text_only,
                "rows": serializable_rows,
                "weekly_schemes": {
                    name: dict(counts) for name, counts in schemes.items()
                },
                "promise": [promise[0], str(promise[1])],
                "html_artifact": html_artifact.name,
                "csv_artifact": csv_artifact.name,
            }
            await self._save_state(chat_id, state)

            primary = [row for row in rows if row["primary"]]
            assigned = [row for row in primary if row["status"] == "ASSIGNED"]
            blocked = [row for row in primary if row["status"] == "BLOCKED"]
            return {
                "mode": self._mode,
                "start_date": str(start),
                "days": days,
                "assigned": len(assigned),
                "blocked": len(blocked),
                "skipped": sum(row["status"] == "SKIPPED" for row in primary),
                "by_channel": dict(Counter(row["channel"] for row in assigned)),
                "by_route": dict(Counter(row["route"] for row in assigned)),
                "weekly_schemes": state["weekly_schemes"],
                "block_reasons": dict(Counter(row["reason"] for row in blocked)),
                "html_report": html_artifact.name,
                "csv_report": csv_artifact.name,
                "note": (
                    "Симулятор назначает коммуникации, но не моделирует погашения "
                    "или причинный эффект стратегии. Дневные скоры учебные; время "
                    "контактов не моделируется."
                ),
            }

    async def inspect_client_plan(
        self,
        *,
        chat_id: str,
        client_id: int,
        plan_date: str | None = None,
        offset: int = 0,
        limit: int = 30,
    ) -> dict[str, Any]:
        if type(client_id) is not int or client_id <= 0:
            raise ValueError("client_id должен быть положительным целым числом")
        if plan_date:
            try:
                date.fromisoformat(plan_date)
            except ValueError as exc:
                raise ValueError("plan_date должен иметь формат YYYY-MM-DD") from exc
        if type(offset) is not int or offset < 0:
            raise ValueError("offset должен быть целым числом >= 0")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit должен быть целым числом от 1 до 100")

        state = await self._load_state(chat_id)
        if state is None:
            return {"error": "В этом чате ещё нет сохранённой симуляции. Сначала запустите её."}

        all_rows = [
            row for row in state["rows"]
            if row["client"] == client_id
            and (plan_date is None or row["date"] == plan_date)
        ]
        if not all_rows:
            return {
                "client_id": client_id,
                "plan_date": plan_date,
                "error": "Клиент или дата отсутствует в последнем плане",
            }

        primary = [row for row in all_rows if row["primary"]]
        page = all_rows[offset: offset + limit]
        return {
            "client_id": client_id,
            "plan_date": plan_date,
            "simulation_start_date": state["start_date"],
            "simulation_days": state["days"],
            "total_client_days": len(primary),
            "assigned": sum(row["status"] == "ASSIGNED" for row in primary),
            "blocked": sum(row["status"] == "BLOCKED" for row in primary),
            "skipped": sum(row["status"] == "SKIPPED" for row in primary),
            "offset": offset,
            "limit": limit,
            "returned_rows": len(page),
            "total_rows": len(all_rows),
            "has_more": offset + len(page) < len(all_rows),
            "next_offset": offset + len(page) if offset + len(page) < len(all_rows) else None,
            "details": page,
        }

    async def delete_chat(self, chat_id: str) -> None:
        await self._ensure_database()
        await asyncio.to_thread(self._delete_chat_sync, chat_id)

    async def close(self) -> None:
        # State operations use short-lived sqlite3 connections, so there is no
        # process-wide DB handle to close. The async method keeps Application's
        # resource lifecycle uniform across services.
        return None

    async def _load_data(self) -> dict[str, list[dict]]:
        if self._mode == "demo":
            return build_demo_data()
        return await asyncio.to_thread(self._load_greenplum_data)

    def _load_greenplum_data(self) -> dict[str, list[dict]]:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - install-time failure
            raise RuntimeError(
                "Greenplum mode requires psycopg. Install requirements.txt."
            ) from exc

        cfg = self._greenplum
        if cfg is None or not all([cfg.host, cfg.database, cfg.user, cfg.password]):
            raise RuntimeError(
                "Greenplum mode requires GP_HOST, GP_DATABASE, GP_USER and GP_PASSWORD"
            )
        from psycopg.rows import dict_row

        queries = {
            "clients": "SELECT * FROM collection_sim.client WHERE active",
            "loans": "SELECT * FROM collection_sim.loan WHERE is_active",
            "scores": "SELECT * FROM collection_sim.client_score",
            "priorities": "SELECT * FROM collection_sim.client_channel_priority",
            "channels": "SELECT * FROM collection_sim.channel WHERE is_enabled",
            "slots": "SELECT * FROM collection_sim.strategy_slot",
            "strategies": "SELECT * FROM collection_sim.strategy WHERE is_enabled",
            "policy": "SELECT * FROM collection_sim.routing_policy WHERE policy_id = 1",
            "limits": "SELECT * FROM collection_sim.contact_limit",
            "holidays": "SELECT holiday_date FROM collection_sim.holiday_calendar",
        }
        with psycopg.connect(
            host=cfg.host,
            port=cfg.port,
            dbname=cfg.database,
            user=cfg.user,
            password=cfg.password,
            connect_timeout=5,
        ) as conn:
            conn.read_only = True
            data: dict[str, list[dict]] = {}
            for name, query in queries.items():
                with conn.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(query)
                    data[name] = cursor.fetchall()
            return data

    def _validate_data(self, data: dict[str, list[dict]]) -> None:
        if len(data.get("policy", [])) != 1:
            raise ValueError("Ожидается ровно одна политика маршрутизации policy_id=1")
        if not data.get("clients") or not data.get("scores"):
            raise ValueError("В симуляторе нет клиентов или исходных скоров")

        enabled = {
            row["channel_code"] for row in data.get("channels", []) if row.get("is_enabled")
        }
        if not enabled:
            raise ValueError("Нет разрешённых каналов коммуникации")
        for slot in data.get("slots", []):
            if slot["channel_code"] not in enabled:
                raise ValueError(
                    f"Стратегия ссылается на выключенный/неизвестный канал: {slot['channel_code']}"
                )
        for score in data["scores"]:
            for field in ("self_cure_score", "repayment_score"):
                value = float(score[field])
                if not 0.0 <= value <= 1.0:
                    raise ValueError(
                        f"{field} клиента {score['client_id']} должен быть в диапазоне [0, 1]"
                    )

    def _validate_scenario(
        self,
        *,
        days: int,
        intensity: str | None,
        text_only: bool,
        route: str | None,
        weekly_contacts: int | None,
        min_calls: int,
    ) -> str | None:
        if type(days) is not int or not 1 <= days <= 365:
            raise ValueError("days должен быть целым числом от 1 до 365")
        if intensity is not None and not isinstance(intensity, str):
            raise ValueError("intensity должен быть строкой")
        if type(text_only) is not bool:
            raise ValueError("text_only должен быть boolean")
        if type(min_calls) is not int or min_calls < 0:
            raise ValueError("min_calls должен быть целым неотрицательным числом")

        if weekly_contacts is None:
            if route is not None or min_calls:
                raise ValueError("route и min_calls требуют weekly_contacts")
            return intensity

        if route not in {"INTENSIVE", "STANDARD", "LIGHT"}:
            raise ValueError(
                "Для weekly_contacts укажи route: INTENSIVE, STANDARD или LIGHT"
            )
        if type(weekly_contacts) is not int or not 0 <= weekly_contacts <= 5:
            raise ValueError("weekly_contacts должен быть целым числом от 0 до 5")
        if min_calls > 2:
            raise ValueError(
                "Более двух DIRECT-контактов в календарную неделю превышает демо-лимит"
            )
        if min_calls > weekly_contacts:
            raise ValueError("Минимум звонков больше общего числа контактов")
        if text_only and min_calls:
            raise ValueError("text_only несовместим с min_calls > 0")
        if intensity:
            raise ValueError("Передай либо intensity, либо weekly_contacts")

        channels = ["CALL"] * min_calls
        message_count = weekly_contacts - min_calls
        message_cycle = ["SMS", "PUSH", "EMAIL", "SMS", "PUSH"]
        channels.extend(message_cycle[:message_count])
        counts = Counter(channels)
        specification = ",".join(
            f"{channel}:{count}" for channel, count in counts.items()
        )
        return f"{route}={specification}"

    async def _ensure_database(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_database_sync)
            self._initialized = True

    def _initialize_database_sync(self) -> None:
        self._state_database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._state_database, timeout=30) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(STATE_SCHEMA)
            connection.commit()

    async def _save_state(self, chat_id: str, payload: dict[str, Any]) -> None:
        await self._ensure_database()
        serialized = json.dumps(payload, ensure_ascii=False)
        await asyncio.to_thread(self._save_state_sync, chat_id, serialized)

    def _save_state_sync(self, chat_id: str, payload: str) -> None:
        with sqlite3.connect(self._state_database, timeout=30) as connection:
            connection.execute(
                """
                INSERT INTO collection_simulation_state(chat_id, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, payload),
            )
            connection.commit()

    async def _load_state(self, chat_id: str) -> dict[str, Any] | None:
        await self._ensure_database()
        payload = await asyncio.to_thread(self._load_state_sync, chat_id)
        return None if payload is None else json.loads(payload)

    def _load_state_sync(self, chat_id: str) -> str | None:
        with sqlite3.connect(self._state_database, timeout=30) as connection:
            row = connection.execute(
                "SELECT payload FROM collection_simulation_state WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def _delete_chat_sync(self, chat_id: str) -> None:
        with sqlite3.connect(self._state_database, timeout=30) as connection:
            connection.execute(
                "DELETE FROM collection_simulation_state WHERE chat_id = ?",
                (chat_id,),
            )
            connection.commit()


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (str(value) if isinstance(value, date) else value)
        for key, value in row.items()
    }


def _rows_to_csv(rows: list[dict[str, Any]]) -> str:
    fields = [
        "date", "client", "loan", "route", "self_cure", "repayment",
        "channel", "status", "reason", "primary",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(_serialize_row(row))
    return buffer.getvalue()
