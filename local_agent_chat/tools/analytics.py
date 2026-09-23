from __future__ import annotations

from collections.abc import Sequence

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from local_agent_chat.agent_context import AgentContext
from local_agent_chat.analytics import DataAnalysisService


def create_analytics_tools(analytics: DataAnalysisService) -> Sequence[BaseTool]:
    @tool
    async def inspect_dataset(
        path: str,
        runtime: ToolRuntime[AgentContext],
        sheet_name: str = "",
        sample_rows: int = 5,
    ) -> str:
        """Inspect an uploaded CSV/TSV/XLSX dataset before doing deeper EDA.

        Returns shape, dtypes, duplicates, missingness and a small sample. For
        Excel, omit sheet_name to use the first sheet; the result reports all
        available sheets. This tool reads only files from the current chat.
        """
        try:
            result = await analytics.inspect_dataset(
                chat_id=runtime.context.chat_id,
                path=path,
                sheet_name=sheet_name or None,
                sample_rows=sample_rows,
            )
            return result.text
        except (FileNotFoundError, ValueError) as error:
            return f"Unable to inspect dataset {path!r}: {error}"

    @tool
    async def build_eda_report(
        path: str,
        runtime: ToolRuntime[AgentContext],
        target: str = "",
        sheet_name: str = "",
    ) -> str:
        """Build a self-contained HTML EDA report for an uploaded CSV/TSV/XLSX.

        The report includes overview, data quality, missingness, numeric and
        categorical summaries, distributions, correlations, sample rows and an
        optional target section. It writes only to the current chat's controlled
        artifacts directory; it does not provide arbitrary file-write access.
        """
        try:
            result = await analytics.build_eda_report(
                chat_id=runtime.context.chat_id,
                path=path,
                target=target or None,
                sheet_name=sheet_name or None,
            )
        except (FileNotFoundError, ValueError) as error:
            return f"Unable to build EDA report for {path!r}: {error}"

        return (
            f"EDA artifact created: {result.artifact.name}\n"
            f"{result.summary}\n"
            "The HTML file will be attached to the assistant response."
        )

    return (inspect_dataset, build_eda_report)
