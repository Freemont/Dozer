# pylint: skip-file
from discord import MessageType
from discord.ext.commands import has_permissions, bot_has_permissions, BucketType, cooldown
from ._utils import *
import asyncio
import discord
import random
import re

# as the name implies, this cog is hilariously hacky code.
# it's very ftc server specific code, made specifically for its own needs.
# i stuck it in git for maintenance purposes.
# this should be deleted from the plowie tree if found.

FTC_DISCORD_ID = 225450307654647808
VERIFY_CHANNEL_ID = 333612583409942530
JOINED_LOGS_ID = 350482751335432202
FEEDS_CHANNEL_ID = 320719178132881408
VOTE_CHANNEL_IDS = []
MEDIA_CHANNEL_ID = 676583549561995274
# feeds, media, robot-showcase
PUBLIC_CHANNEL_IDS = [320719178132881408, 676583549561995274, 771188718198456321]
# media, robot-showcase
EMBED_ONLY_CHANNEL_IDS = [676583549561995274, 771188718198456321]
THREAD_MESSAGE_REMOVE_CHANNEL_IDS = [674081079761829898]

class Hacks(Cog):

    @Cog.listener()
    async def on_member_join(self, member):
        if member.guild.id != FTC_DISCORD_ID:
            return
        logs = self.bot.get_channel(JOINED_LOGS_ID)
        res = f"```New user {member} ({member.id})\nInvite summary:\n"
        for i in await member.guild.invites():
            res += f"{i.code}, {i.uses}\n"
        res += "```"
        await logs.send(res)

    async def handle_verification(self, message):
        member = message.author
        if message.channel.id == VERIFY_CHANNEL_ID and message.content.lower().startswith("i have read the rules and regulations"):
            await member.add_roles(discord.utils.get(message.guild.roles, name="Member"))
            await member.send("""Thank you for reading the rules and regulations. We would like to welcome you to the FIRST® Tech Challenge Discord Server! Please follow the server rules and have fun! Don't hesitate to ping a member of the moderation team if you have any questions!

_Please set your nickname with `%nick NAME - TEAM#` in #bot-spam to reflect your team number, or your role in FIRST Robotics if you are not affiliated with a team. If you are not a part of or affiliated directly with a FIRST® Tech Challenge team or the program itself, please contact an administrator for further details._""")
            await member.edit(nick=(message.author.display_name[:20] + " | SET TEAM#"))

    async def handle_talking_embed_only(self,msg):
        """Checks for messages sent in embed only channels without attachments or embeds and automatically deletes them."""
        if (msg.channel.id in EMBED_ONLY_CHANNEL_IDS) and not msg.attachments and not msg.embeds and not re.search("https?://", msg.content):
            await msg.delete()

    async def handle_public_channels(self, message):
        if message.channel.id in PUBLIC_CHANNEL_IDS:
            await message.publish()

    async def handle_vote_channels(self, message):
        if message.channel.id in VOTE_CHANNEL_IDS and message.type in [MessageType.default, MessageType.reply]:
            await message.add_reaction('👍')
            await message.add_reaction('👎')

    @Cog.listener()
    async def on_message(self, message):
        await asyncio.gather(
            self.handle_verification(message),
            self.handle_talking_embed_only(message),
            self.handle_public_channels(message),
            self.handle_vote_channels(message),
        )

    @has_permissions(manage_roles=True)
    @bot_has_permissions(manage_roles=True)
    @command()
    async def forceundeafen(self, ctx, member: discord.Member):
        async with ctx.typing():
            await ctx.bot.cogs["Moderation"].perm_override(member, read_messages=None)
        await ctx.send(f"Overwrote perms for {member}")
        #ctx.bot.cogs["Moderation"].permoverride(user=member

    @has_permissions(manage_roles=True)
    @bot_has_permissions(manage_roles=True)
    @command()
    async def takeemotes(self, ctx, member: discord.Member):
        async with ctx.typing():
            await ctx.bot.cogs["Moderation"].perm_override(member, external_emojis=False)
        await ctx.send("took away external emote perms for {member}")

    @has_permissions(manage_roles=True)
    @bot_has_permissions(manage_roles=True)
    @command()
    async def giveemotes(self, ctx, member: discord.Member):
        async with ctx.typing():
            await ctx.bot.cogs["Moderation"].perm_override(member, external_emojis=None)
        await ctx.send("reset external emote perms for {member}")

    @has_permissions(add_reactions=True)
    @bot_has_permissions(add_reactions=True)
    @command()
    async def vote(self, ctx):
        await ctx.message.add_reaction('👍')
        await ctx.message.add_reaction('👎')


    @cooldown(1, 60, BucketType.user)
    @bot_has_permissions(embed_links=True)
    @command()
    async def sleep(self, ctx, member: discord.Member = None):
        IMG_URL = "https://i.imgur.com/ctzynlC.png"
        await ctx.send(IMG_URL)
        if member:
            await member.send("🛌 **GO TO SLEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEP** 🛌")

    @Cog.listener
    async def on_thread_create(self, thread):
        if thread.parent.id in THREAD_MESSAGE_REMOVE_CHANNEL_IDS:
            message = thread.message
            if (message.type == MessageType.thread_created and message.thread and message.thread.id == thread.id):
                await message.delete()
                
async def setup(bot):
    await bot.add_cog(Hacks(bot))
