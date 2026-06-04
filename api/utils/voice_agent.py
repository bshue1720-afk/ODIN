"""
ODIN Voice Agent (LiveKit Agents 1.x)
STT: Deepgram Nova-3
LLM: Anthropic Claude Haiku
TTS: Cartesia (Grant voice)
VAD: Silero
"""
import os
import asyncio
import logging

from livekit import agents
from livekit.agents import Agent, AgentSession, RoomInputOptions, function_tool
from livekit.plugins import deepgram, cartesia, silero

# Import tools at module level — never lazy-import inside a tool call
from utils.voice_tools import (
    get_hot_leads,
    get_pending_approvals,
    get_pending_decisions,
    get_blast_stats,
    get_morning_briefing,
    approve_deal as _approve_deal,
    spawn_agent_task,
    send_slack_note,
)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are ODIN, Brock's AI chief of staff. You are on a live phone call. "
    "Rules: speak naturally, no lists or markdown, max 2 sentences per reply unless giving a briefing. "
    "When asked to research or find information, call spawn_task immediately — do not ask for permission first. "
    "When asked to post a note, call slack_note immediately. "
    "Only confirm before approving a deal (financial action). "
    "Always speak the result of tool calls out loud."
)


class OdinVoiceAgent(Agent):
    def __init__(self):
        super().__init__(instructions=SYSTEM_PROMPT)

    @function_tool
    async def hot_leads(self) -> str:
        """Get current hot leads scored 9 or higher."""
        try:
            return await asyncio.to_thread(get_hot_leads)
        except Exception as e:
            return f"I couldn't pull hot leads right now: {e}"

    @function_tool
    async def pending_approvals(self) -> str:
        """Get deals waiting for Brock's approval."""
        try:
            return await asyncio.to_thread(get_pending_approvals)
        except Exception as e:
            return f"I couldn't pull approvals: {e}"

    @function_tool
    async def pending_decisions(self) -> str:
        """Get open decisions that need an outcome recorded."""
        try:
            return await asyncio.to_thread(get_pending_decisions)
        except Exception as e:
            return f"I couldn't pull decisions: {e}"

    @function_tool
    async def blast_stats(self) -> str:
        """Get the last text blast results."""
        try:
            return await asyncio.to_thread(get_blast_stats)
        except Exception as e:
            return f"I couldn't pull blast stats: {e}"

    @function_tool
    async def morning_briefing(self) -> str:
        """Give a full morning briefing: leads, approvals, decisions, blast."""
        try:
            return await asyncio.to_thread(get_morning_briefing)
        except Exception as e:
            return f"Briefing failed: {e}"

    @function_tool
    async def approve_deal(self, approval_id: str) -> str:
        """
        Approve a pending deal by ID.
        approval_id: partial or full approval queue ID
        """
        try:
            return await asyncio.to_thread(_approve_deal, approval_id)
        except Exception as e:
            return f"Approval failed: {e}"

    @function_tool
    async def spawn_task(self, task_description: str) -> str:
        """
        Launch a research or planning task. Fires immediately in the background.
        Results post to Slack. Call this for ANY research, sourcing, or planning request.
        task_description: what to research or build
        """
        try:
            result = await asyncio.to_thread(spawn_agent_task, task_description)
            return result
        except Exception as e:
            return f"I had trouble launching that task: {e}. Try again or check Slack."

    @function_tool
    async def slack_note(self, message: str) -> str:
        """
        Post a note or reminder to Slack.
        message: what to post
        """
        try:
            return await asyncio.to_thread(send_slack_note, message)
        except Exception as e:
            return f"Couldn't post to Slack: {e}"


async def entrypoint(ctx: agents.JobContext):
    """LiveKit agent entrypoint — called for every new room/call."""
    log.info(f"[voice_agent] New session: room={ctx.room.name}")

    await ctx.connect(auto_subscribe=agents.AutoSubscribe.AUDIO_ONLY)

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=_build_llm(),
        tts=cartesia.TTS(
            voice=os.environ.get("CARTESIA_VOICE_ID", "d46abd1d-2d02-43e8-819f-51fb652c1c61"),
        ),
        vad=silero.VAD.load(),
    )

    await session.start(
        room=ctx.room,
        agent=OdinVoiceAgent(),
        room_input_options=RoomInputOptions(),
    )

    await session.generate_reply(
        instructions="Greet Brock as ODIN. Keep it under 8 words."
    )


def _build_llm():
    try:
        from livekit.plugins import anthropic as lk_anthropic
        return lk_anthropic.LLM(
            model="claude-haiku-4-5-20251001",
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
    except ImportError:
        from livekit.plugins import openai as lk_openai
        return lk_openai.LLM(model="gpt-4o-mini")
