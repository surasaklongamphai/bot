import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta

import discord
from async_rediscache import RedisCache
from dateutil import parser
from discord import Colour, Member, VoiceState
from discord.ext.commands import Cog, Context, command

from bot.api import ResponseCodeError
from bot.bot import Bot
from bot.constants import Channels, Event, MODERATION_ROLES, Roles, VoiceGate as GateConf
from bot.decorators import has_no_roles, in_whitelist
from bot.exts.moderation.modlog import ModLog
from bot.utils.checks import InWhitelistCheckFailure

log = logging.getLogger(__name__)

NO_MSG = 0

FAILED_MESSAGE = (
    """You are not currently eligible to use voice inside Python Discord for the following reasons:\n\n{reasons}"""
)

MESSAGE_FIELD_MAP = {
    "verified_at": f"have been verified for less than {GateConf.minimum_days_verified} days",
    "voice_banned": "have an active voice ban infraction",
    "total_messages": f"have sent less than {GateConf.minimum_messages} messages",
    "activity_blocks": f"have been active for fewer than {GateConf.minimum_activity_blocks} ten-minute blocks",
}

VOICE_PING = (
    "Wondering why you can't talk in the voice channels? "
    "Use the `!voiceverify` command in here to verify. "
    "If you don't yet qualify, you'll be told why!"
)


class VoiceGate(Cog):
    """Voice channels verification management."""

    # RedisCache[t.Union[discord.User.id, discord.Member.id], t.Union[discord.Message.id, int]]
    # The cache's keys are the IDs of members who are verified or have joined a voice channel
    # The cache's values are either the message ID of the ping message or 0 (NO_MSG) if no message is present
    redis_cache = RedisCache()

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        # voice_verification_channel set to None so that we have a placeholder to get it later in the cog
        self.voice_verification_channel = None

    @property
    def mod_log(self) -> ModLog:
        """Get the currently loaded ModLog cog instance."""
        return self.bot.get_cog("ModLog")

    @command(aliases=('voiceverify',))
    @has_no_roles(Roles.voice_verified)
    @in_whitelist(channels=(Channels.voice_gate,), redirect=None)
    async def voice_verify(self, ctx: Context, *_) -> None:
        """
        Apply to be able to use voice within the Discord server.

        In order to use voice you must meet all three of the following criteria:
        - You must have over a certain number of messages within the Discord server
        - You must have accepted our rules over a certain number of days ago
        - You must not be actively banned from using our voice channels
        - You must have been active for over a certain number of 10-minute blocks
        """
        # If user has received a ping in voice_verification, delete the message
        if message_id := await self.redis_cache.get(ctx.author.id, NO_MSG):
            with suppress(discord.NotFound):
                self.voice_verification_channel = self.bot.get_channel(Channels.voice_gate)
                await self.bot.http.delete_message(self.voice_verification_channel, message_id)
            await self.redis_cache.set(ctx.author.id, NO_MSG)

        try:
            data = await self.bot.api_client.get(f"bot/users/{ctx.author.id}/metricity_data")
        except ResponseCodeError as e:
            if e.status == 404:
                embed = discord.Embed(
                    title="Not found",
                    description=(
                        "We were unable to find user data for you. "
                        "Please try again shortly, "
                        "if this problem persists please contact the server staff through Modmail."
                    ),
                    color=Colour.red()
                )
                log.info(f"Unable to find Metricity data about {ctx.author} ({ctx.author.id})")
            else:
                embed = discord.Embed(
                    title="Unexpected response",
                    description=(
                        "We encountered an error while attempting to find data for your user. "
                        "Please try again and let us know if the problem persists."
                    ),
                    color=Colour.red()
                )
                log.warning(f"Got response code {e.status} while trying to get {ctx.author.id} Metricity data.")

            await ctx.author.send(embed=embed)
            return

        # Pre-parse this for better code style
        if data["verified_at"] is not None:
            data["verified_at"] = parser.isoparse(data["verified_at"])
        else:
            data["verified_at"] = datetime.utcnow() - timedelta(days=3)

        checks = {
            "verified_at": data["verified_at"] > datetime.utcnow() - timedelta(days=GateConf.minimum_days_verified),
            "total_messages": data["total_messages"] < GateConf.minimum_messages,
            "voice_banned": data["voice_banned"],
            "activity_blocks": data["activity_blocks"] < GateConf.minimum_activity_blocks
        }
        failed = any(checks.values())
        failed_reasons = [MESSAGE_FIELD_MAP[key] for key, value in checks.items() if value is True]
        [self.bot.stats.incr(f"voice_gate.failed.{key}") for key, value in checks.items() if value is True]

        if failed:
            embed = discord.Embed(
                title="Voice Gate failed",
                description=FAILED_MESSAGE.format(reasons="\n".join(f'• You {reason}.' for reason in failed_reasons)),
                color=Colour.red()
            )
            try:
                await ctx.author.send(embed=embed)
                await ctx.send(f"{ctx.author}, please check your DMs.")
            except discord.Forbidden:
                await ctx.channel.send(ctx.author.mention, embed=embed)
            return

        self.mod_log.ignore(Event.member_update, ctx.author.id)
        embed = discord.Embed(
            title="Voice gate passed",
            description="You have been granted permission to use voice channels in Python Discord.",
            color=Colour.green()
        )

        if ctx.author.voice:
            embed.description += "\n\nPlease reconnect to your voice channel to be granted your new permissions."

        try:
            await ctx.author.send(embed=embed)
            await ctx.send(f"{ctx.author}, please check your DMs.")
        except discord.Forbidden:
            await ctx.channel.send(ctx.author.mention, embed=embed)

        # wait a little bit so those who don't get DMs see the response in-channel before losing perms to see it.
        await asyncio.sleep(3)
        await ctx.author.add_roles(discord.Object(Roles.voice_verified), reason="Voice Gate passed")

        self.bot.stats.incr("voice_gate.passed")

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Delete all non-staff messages from voice gate channel that don't invoke voice verify command."""
        # Check is channel voice gate
        if message.channel.id != Channels.voice_gate:
            return

        ctx = await self.bot.get_context(message)
        is_verify_command = ctx.command is not None and ctx.command.name == "voice_verify"

        # When it's a bot sent message, delete it after some time
        if message.author.bot:
            # Comparing the message with the voice ping constant
            if message.content.endswith(VOICE_PING):
                log.trace("Message is the voice verification ping. Ignore.")
                return
            with suppress(discord.NotFound):
                await message.delete(delay=GateConf.bot_message_delete_delay)
                return

        # Then check is member moderator+, because we don't want to delete their messages.
        if any(role.id in MODERATION_ROLES for role in message.author.roles) and is_verify_command is False:
            log.trace(f"Excluding moderator message {message.id} from deletion in #{message.channel}.")
            return

        # Ignore deleted voice verification messages
        if ctx.command is not None and ctx.command.name == "voice_verify":
            self.mod_log.ignore(Event.message_delete, message.id)

        with suppress(discord.NotFound):
            await message.delete()

    @Cog.listener()
    async def on_voice_state_update(self, member: Member, before: VoiceState, after: VoiceState) -> None:
        """Pings a user if they've never joined the voice chat before and aren't voice verified."""
        if member.bot:
            log.trace("User is a bot. Ignore.")
            return

        # member.voice will return None if the user is not in a voice channel
        if member.voice is None:
            log.trace("User not in a voice channel. Ignore.")
            return

        in_cache = await self.redis_cache.get(member.id, NO_MSG)
        if in_cache:
            log.trace("User already in cache. Ignore.")
            return

        log.trace("User not in cache and is in a voice channel.")
        verified = any(Roles.voice_verified == role.id for role in member.roles)
        if verified:
            log.trace("User is verified, add to the cache and ignore.")
            await self.redis_cache.set(member.id, NO_MSG)
            return

        log.trace("User is unverified. Send ping.")
        await self.bot.wait_until_guild_available()
        voice_verification_channel = self.bot.get_channel(Channels.voice_gate)

        message = await voice_verification_channel.send(f"Hello, {member.mention}! {VOICE_PING}")
        await self.redis_cache.set(member.id, message.id)

        await asyncio.sleep(60)
        if message := await self.redis_cache.get(member.id):
            await message.delete()
            await self.redis_cache.set(member.id, NO_MSG)

    async def cog_command_error(self, ctx: Context, error: Exception) -> None:
        """Check for & ignore any InWhitelistCheckFailure."""
        if isinstance(error, InWhitelistCheckFailure):
            error.handled = True


def setup(bot: Bot) -> None:
    """Loads the VoiceGate cog."""
    bot.add_cog(VoiceGate(bot))
