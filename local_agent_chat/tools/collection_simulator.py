from __future__ import annotations

import json
from collections.abc import Sequence

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from local_agent_chat.agent_context import AgentContext
from local_agent_chat.collection import CollectionSimulationService


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def create_collection_simulator_tools(
    service: CollectionSimulationService,
) -> Sequence[BaseTool]:
    @tool
    async def collection_portfolio_summary(
        runtime: ToolRuntime[AgentContext],
    ) -> str:
        """Return current client counts by collection route.

        Use this instead of guessing portfolio sizes or route distribution.
        In demo mode it uses the embedded deterministic fixture; in Greenplum
        mode it reads the configured collection_sim tables.
        """
        try:
            return _json(await service.portfolio_summary())
        except (RuntimeError, ValueError) as error:
            return _json({"error": str(error)})

    @tool
    async def simulate_collection_strategy(
        days: int,
        runtime: ToolRuntime[AgentContext],
        intensity: str = "",
        text_only: bool = False,
        route: str = "",
        weekly_contacts: int | None = None,
        min_calls: int = 0,
    ) -> str:
        """Run the collection communication-assignment simulator.

        `days` is 1..365. `intensity` may override one route, e.g.
        `INTENSIVE=ROBOT:2,SMS:2,PUSH:1`. Alternatively set `route` plus
        `weekly_contacts` (0..5) and optional `min_calls` (0..2). `text_only`
        keeps SMS/PUSH/EMAIL only. Produces HTML and CSV artifacts.
        """
        try:
            result = await service.simulate_strategy(
                chat_id=runtime.context.chat_id,
                days=days,
                intensity=intensity or None,
                text_only=text_only,
                route=route or None,
                weekly_contacts=weekly_contacts,
                min_calls=min_calls,
            )
            return _json(result)
        except (RuntimeError, ValueError) as error:
            return _json({"error": str(error)})

    @tool
    async def inspect_client_plan(
        client_id: int,
        runtime: ToolRuntime[AgentContext],
        plan_date: str = "",
        offset: int = 0,
        limit: int = 30,
    ) -> str:
        """Inspect one client's latest saved simulation for this chat.

        Optionally filter by `plan_date` in YYYY-MM-DD format. Use `offset` and
        `limit` to page through long plans; the result returns `next_offset`.
        """
        try:
            return _json(
                await service.inspect_client_plan(
                    chat_id=runtime.context.chat_id,
                    client_id=client_id,
                    plan_date=plan_date or None,
                    offset=offset,
                    limit=limit,
                )
            )
        except (RuntimeError, ValueError) as error:
            return _json({"error": str(error)})

    return (
        collection_portfolio_summary,
        simulate_collection_strategy,
        inspect_client_plan,
    )
