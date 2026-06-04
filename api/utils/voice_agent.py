"""
ODIN Voice Agent (LiveKit Agents 1.x)
Handles inbound SIP calls and outbound call sessions.
STT: Deepgram Nova-3
LLM: Anthropic Claude Haiku (fast, low-latency)
TTS: ElevenLabs
VAD: Silero
"""
import os
import asyncio
import logging

from livekit import agents
from livekit.agents import Agent, AgentSession, RoomInputOptions, function_tool
from livekit.plugins import deepgram, cartesia, silero

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are ODIN, Brock's AI chief of staff. You're on a live phone call — speak naturally, no lists or markdown, max 2 sentences per reply unless giving a briefing. Use your tools to answer questions about leads, deals, approvals, and finances. Spawn agent tasks for research or planning requests and post results to Slack. Confirm before taking any action."""


class OdinVoiceAgent(Agent):
    def __init__(self):
        super().__init__(instructions=SYSTEM_PROMPT)

    @function_tool
    async def hot_leads(self) -> str:
        """Get current hot leads (score 9+)."""
        from utils.voice_tools import get_hot_leads
        return await asyncio.to_thread(get_hot_leads)

    @function_tool
    async def pending_approvals(self) -> str:
        """Get deals waiting for Brock's approval."""
        from utils.voice_tools import get_pending_approvals
        return await asyncio.to_thread(get_pending_approvals)

    @function_tool
    async def pending_decisions(self) -> str:
        """Get open decisions that need an outcome recorded."""
        from utils.voice_tools import get_pending_decisions
        return await asyncio.to_thread(get_pending_decisions)

    @function_tool
    async def blast_stats(self) -> str:
        """Get the last text blast results."""
        from utils.voice_tools import get_blast_stats
        return await asyncio.to_thread(get_blast_stats)

    @function_tool
    async def morning_briefing(self) -> str:
        """Give a full morning briefing: leads, approvals, decisions, blast."""
        from utils.voice_tools import get_morning_briefing
        return await asyncio.to_thread(get_morning_briefing)

    @function_tool
    async def approve_deal(self, approval_id: str) -> str:
        """
        Approve a pending deal.
        approval_id: partial or full approval queue ID
        """
        from utils.voice_tools import approve_deal
        return await asyncio.to_thread(approve_deal, approval_id)

    @function_tool
    async def spawn_task(self, task_description: str) -> str:
        """
        Spawn an agent pipeline for any task — research, business plans, proposals, etc.
        Full output will be posted to Slack.
        task_description: what to build or research
        """
        from utils.voice_tools import spawn_agent_task
        return await asyncio.to_thread(spawn_agent_task, task_description)

    @function_tool
    async def slack_note(self, message: str) -> str:
        """
        Post a note or reminder to Slack.
        message: what to post
        """
        from utils.voice_tools import send_slack_note
        return await asyncio.to_thread(send_slack_note, message)


async def entrypoint(ctx: agents.JobContext):
    """LiveKit agent entrypoint — called for every new room/call."""
    log.info(f"[voice_agent] New session: room={ctx.room.name}")

    await ctx.connect(auto_subscribe=agents.AutoSubscribe.AUDIO_ONLY)

    # Build the voice pipeline
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

    # Greet the caller
    await session.generate_reply(
        instructions="Greet Brock as ODIN and ask what he needs. Keep it under 10 words."
    )


def _build_llm():
    """Build the LLM — uses Anthropic via livekit plugin."""
    try:
        from livekit.plugins import anthropic as lk_anthropic
        return lk_anthropic.LLM(
            model="claude-haiku-4-5-20251001",
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
    except ImportError:
        # Fallback: openai if anthropic plugin not available
        from livekit.plugins import openai as lk_openai
        return lk_openai.LLM(model="gpt-4o-mini")
