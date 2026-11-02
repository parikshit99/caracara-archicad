"""Asking the user directly, in their client, before something they should decide themselves.

Where the client supports MCP form elicitation the server asks the user and ignores the AI's
'confirm', so the AI cannot answer for the user. Other clients keep 'confirm': true, which
relies on the AI having asked.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import AcceptedElicitation, CancelledElicitation, Context, DeclinedElicitation, Elicit
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from caracara_archicad_mcp.safety import ensure_allowed, irreversible_reason
from caracara_archicad_mcp.tools.tool_registry import TOOL_DISCOVERY_CATALOG

# What a resolver that asks the user found: the user's answer, or a plain value when nobody was asked.
UserAnswer = AcceptedElicitation[Any] | DeclinedElicitation | CancelledElicitation | None


class GoAhead(BaseModel):
    """The user's answer when asked: accepting the question is the go-ahead."""


def client_can_ask(ctx: Context) -> bool:
    """True when the client can show the user a question (MCP form elicitation)."""
    elicitation = ctx.client_capabilities.elicitation if ctx.client_capabilities else None
    # A client from before elicitation modes declares the capability empty, which means forms.
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


def _irreversible(name: str) -> tuple[str, str] | None:
    """The Archicad command behind a tool name and why it cannot be taken back, if it cannot."""
    command = TOOL_DISCOVERY_CATALOG.get(name, {}).get("title")
    reason = irreversible_reason(command) if command else None
    return (command, reason) if reason else None


async def ask_before_irreversible(name: str, arguments: dict, ctx: Context) -> Elicit[GoAhead] | None:
    """Ask the user before archicad_call_tool runs a command that Undo cannot take back.

    None: nobody was asked, because the command can be undone, is refused anyway, or the client cannot ask.
    """
    irreversible = _irreversible(name)
    if irreversible is None or not client_can_ask(ctx):
        return None
    command, reason = irreversible
    try:
        ensure_allowed(command)
    except ToolError:
        # archicad_call_tool refuses it itself, so there is nothing to ask about.
        return None
    return Elicit(f"The AI wants to run {command} in Archicad, which {reason}. Go ahead?", GoAhead)


def confirmed_arguments(name: str, arguments: dict, user_answer: UserAnswer) -> dict:
    """The command's arguments without 'confirm', once the user has agreed to a command Undo cannot take back.

    Raises ToolError when the user said no, or when nobody could be asked and the call has no 'confirm': true.
    """
    irreversible = _irreversible(name)
    if irreversible is None:
        return arguments
    command, reason = irreversible
    ensure_allowed(command)
    if isinstance(user_answer, DeclinedElicitation | CancelledElicitation):
        raise ToolError(
            f"Nothing was run: the user said no to {command} when asked in their client. Do not repeat it "
            "unless the user asks for it again."
        )
    arguments = dict(arguments)
    confirm = arguments.pop("confirm", False)
    # The SDK hands a resolver's plain return value over as accepted too, so only the question's own answer counts.
    user_agreed = isinstance(user_answer, AcceptedElicitation) and isinstance(user_answer.data, GoAhead)
    if not user_agreed and confirm is not True:
        raise ToolError(
            f"{command} {reason}, so it runs only with the user's go-ahead. Ask the user; if they agree, call "
            "again with \"confirm\": true in 'arguments'."
        )
    return arguments
