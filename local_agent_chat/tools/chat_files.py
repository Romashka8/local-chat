from __future__ import annotations

from collections.abc import Sequence

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from local_agent_chat.agent_context import AgentContext
from local_agent_chat.sandbox_files import SandboxFiles


def create_chat_file_tools(sandbox: SandboxFiles) -> Sequence[BaseTool]:
    """Create read-only tools scoped to files uploaded into the current Chat."""

    @tool
    async def list_chat_files(runtime: ToolRuntime[AgentContext]) -> str:
        """List files uploaded to the CURRENT chat.

        Use this when the user refers to an attachment without giving its exact
        stored name, or before reading files when several attachments exist.
        Files from other chats and the host filesystem are not accessible.
        """
        files = await sandbox.list_files(runtime.context.chat_id)
        if not files:
            return "No files are currently stored in this chat."

        lines = ["Files available in the current chat:"]
        for item in files:
            lines.append(f"- {item.name} ({item.size_bytes} bytes)")
        return "\n".join(lines)

    @tool
    async def read_chat_file(
        path: str,
        runtime: ToolRuntime[AgentContext],
        offset: int = 0,
        limit: int = 200,
    ) -> str:
        """Read a text file uploaded to the CURRENT chat.

        `path` must be a filename returned by list_chat_files. `offset` is a
        zero-based line offset and `limit` is the number of lines to return
        (maximum 500). For long files, continue reading using the next offset
        reported by the tool. This tool is read-only and cannot access host
        paths or files from other chats.
        """
        try:
            return await sandbox.read_text(
                runtime.context.chat_id,
                path,
                offset=offset,
                limit=limit,
            )
        except (FileNotFoundError, ValueError) as error:
            return f"Unable to read {path!r}: {error}"

    return (list_chat_files, read_chat_file)
