#  Copyright (c) 2020 — present, Kirill M.
#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at https://mozilla.org/MPL/2.0/.

import io
import random
import asyncio
import functools
from typing import Dict, List, Union

import discord
import humanize
import more_itertools as miter
from aiohttp import ClientResponseError
from discord.ext import commands

from tomodachi.core import CogMixin, TomodachiMenu, TomodachiContext, checks
from tomodachi.utils import helpers, timestamp, avatar_or_default
from tomodachi.core.enums import ActionType
from tomodachi.core.actions import Action
from tomodachi.utils.converters import EntryID, TimeUnit

EmojiProxy = Union[discord.Emoji, discord.PartialEmoji]


class JishoMenu(TomodachiMenu):
    def __init__(self, entries: List[Dict]):
        super().__init__(entries, title=None)
        self.embed.color = 0x56D926

    def make_footer_text(self):
        footer = "Provided by jisho.org"
        if self.max_index > 0:
            footer += f" (Page {self.current_index+1}/{self.max_index+1})"
        return footer

    async def format_embed(self, payload):
        self.embed.clear_fields()

        self.embed.title = payload["japanese"][0].get("word", payload["slug"])
        if jlpt := payload["jlpt"]:
            self.embed.title += " (%s)" % ", ".join(str(level).upper() for level in jlpt)

        if reading := payload["japanese"][0].get("reading"):
            self.embed.add_field(name="Reading", value=reading, inline=False)

        if senses := payload["senses"]:
            definitions = "".join("%s;\n" % ", ".join(sense["english_definitions"]) for sense in senses)
            self.embed.add_field(name="Senses", value=definitions, inline=False)

        self.embed.description = None if not senses[0]["info"] else "\n".join(senses[0]["info"])

        self.embed.set_footer(text=self.make_footer_text())


class Tools(CogMixin, icon="\N{FILE FOLDER}", colour=0x55ACEE):
    @staticmethod
    async def get_image_url(message: discord.Message, user: Union[discord.Member, discord.User] = None):
        url = None

        if message.attachments:
            url = message.attachments[0].url

        elif user is not None:
            url = avatar_or_default(user).url
        return url

    @commands.command()
    @commands.cooldown(1, 4.0, commands.BucketType.user)
    async def jisho(self, ctx: TomodachiContext, *, query: str):
        """Make a query in the online Japanese dictionary provided by jisho"""
        url = "https://jisho.org/api/v1/search/words"
        params = {"keyword": query}

        response = await self.bot.session.get(url, params=params)
        json = await response.json()
        data = json["data"]

        if not data:
            return await ctx.send("\U000026a0 No results were found, try changing the query.")

        menu = JishoMenu(data)
        await menu.start(ctx, channel=ctx.channel)

    @commands.command(aliases=["now"])
    async def unix(self, ctx: TomodachiContext):
        """Current UNIX timestamp"""
        ts = int(helpers.utcnow().timestamp())
        await ctx.send(f"{ts}")

    @commands.command(description='To provide a sentence as one of options, use quotes "word1 word 2"')
    async def choose(self, ctx: TomodachiContext, *options: str):
        """Randomly selects a word or a sentence"""
        selected = discord.utils.escape_markdown(random.choice(options))
        await ctx.send(f"\N{SQUARED KATAKANA KOKO} {selected}")

    @commands.command()
    async def hello(self, ctx: commands.Context):
        await ctx.send(f"Hello, {ctx.author.name}! I'm {ctx.bot.user.name}.")

    @commands.command()
    @commands.cooldown(1, 10.0, commands.BucketType.user)
    async def caption(self, ctx: TomodachiContext, user: Union[discord.Member, discord.User] = None):
        """Caption an image"""
        await ctx.trigger_typing()

        user = user or ctx.author
        image_url = await self.get_image_url(ctx.message, user)

        url = "https://captionbot.azurewebsites.net/api/messages"
        payload = {"Content": image_url, "Type": "CaptionRequest"}

        try:
            async with self.bot.session.post(url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.text()

            e = discord.Embed(title="CaptionBot", description=str(data))
            e.set_image(url=image_url)

            await ctx.send(embed=e)

        except ClientResponseError:
            await ctx.send(":x: API request failed")

    @commands.guild_only()
    @commands.group(aliases=["emote", "e"], help="Group of emoji related commands")
    async def emoji(self, ctx: TomodachiContext):
        if not ctx.invoked_subcommand:
            await ctx.send_help(ctx.command.qualified_name)

    @commands.cooldown(1, 10.0, commands.BucketType.channel)
    @emoji.command(name="list", aliases=["ls"], help="Spawns a menu with a list of emojis of this server")
    async def emoji_list(self, ctx: TomodachiContext):
        lines_chunks = miter.chunked([f"{e} | `{e}`" for e in ctx.guild.emojis], 10)
        pages = ["\n".join(lines) for lines in lines_chunks]

        menu = TomodachiMenu(pages, title=f"Emojis for {ctx.guild.name}")
        await menu.start(ctx)

    @commands.cooldown(1, 10.0, commands.BucketType.guild)
    @commands.bot_has_permissions(manage_emojis=True)
    @commands.has_permissions(manage_emojis=True)
    @emoji.command(name="add", help="Adds new emoji to the server from attached images")
    async def emoji_add(self, ctx: TomodachiContext, name: str):
        if not ctx.message.attachments:
            raise commands.BadArgument("Emoji to upload was not provided as an argument or attachment")

        attachment = ctx.message.attachments[0]
        if attachment.size > 256000:
            return await ctx.send(":x: Your attachment exceeds 256kb file size limit!")

        b = await attachment.read()
        e = await ctx.guild.create_custom_emoji(name=name, image=b)

        await ctx.send(f"{e} has been uploaded")

    @commands.cooldown(1, 7.0, commands.BucketType.user)
    @commands.bot_has_permissions(manage_emojis=True)
    @commands.has_permissions(manage_emojis=True)
    @emoji.command(name="grab", aliases=["steal", "reupload"], help="Steals emojis from other servers")
    async def emoji_grab(self, ctx: TomodachiContext, emojis: commands.Greedy[EmojiProxy]):
        c = 0

        for emoji in emojis:
            if isinstance(emoji, (discord.Emoji, discord.PartialEmoji)):
                if e_guild := getattr(emoji, "guild", None):
                    if e_guild.id == ctx.guild.id:
                        continue

                buff = await emoji.read()
                created_emoji = await ctx.guild.create_custom_emoji(name=emoji.name, image=buff)

                if created_emoji:
                    c += 1

        await ctx.send(f":ok_hand: Uploaded `{c}`/`{len(emojis)}` emojis.")

    @commands.cooldown(1, 5.0, commands.BucketType.user)
    @commands.max_concurrency(1, commands.BucketType.channel)
    @commands.command(help="Transforms text data into speech")
    async def tts(self, ctx: TomodachiContext, language: str, *, text: str):
        url = f"{self.bot.config.BACKEND_URL}/tts"
        body = {"language": language, "text": text}
        headers = {"Authorization": self.bot.config.BACKEND_TOKEN}

        async with self.bot.session.get(url, headers=headers, json=body) as resp:
            if resp.content_type == "audio/mp3":
                b = await resp.read()
            else:
                t = await resp.text()
                return await ctx.reply(t, mention_author=False)

        buff = io.BytesIO(b)
        file = discord.File(buff, "tts.mp3")

        await ctx.reply("Here's your requested text to speech!", file=file, mention_author=False)

    @commands.cooldown(1, 3.0, commands.BucketType.user)
    @commands.command(help="Shows information about colours")
    async def color(self, ctx: TomodachiContext, color: discord.Colour):
        r, g, b = color.to_rgb()

        url = f"{self.bot.config.BACKEND_URL}/square/{r}/{g}/{b}"
        headers = {"Authorization": self.bot.config.BACKEND_TOKEN}
        buff = io.BytesIO()

        async with self.bot.session.get(url, headers=headers) as resp:
            b = await resp.read()

        buff.write(b)
        buff.seek(0)

        file = discord.File(buff, "color.png")

        embed = discord.Embed()
        embed.colour = color

        embed.add_field(name="HEX", value=f"{color}")
        embed.add_field(name="RGB", value=f"{color.to_rgb()}")

        embed.set_thumbnail(url="attachment://color.png")

        await ctx.send(file=file, embed=embed)

    @commands.command(help="Turns time deltas into human readable text")
    async def humanize(self, ctx: TomodachiContext, time_unit: TimeUnit):
        func = functools.partial(humanize.precisedelta, time_unit)
        humanized = await asyncio.to_thread(func)

        await ctx.send(humanized)

    @commands.group(aliases=["r", "reminders"], help="Time based mentions")
    @commands.cooldown(1, 2.5, commands.BucketType.user)
    async def reminder(self, ctx: commands.Context):
        if not ctx.invoked_subcommand:
            await ctx.send(":x: You haven't used any subcommand, please, see help.")

    @checks.reminders_limit()
    @reminder.command(name="add", aliases=["new", "a"], help="Create new reminder")
    async def reminder_add(self, ctx: TomodachiContext, to_wait: TimeUnit, *, text: str = "..."):
        now = helpers.utcnow()
        trigger_at = now + to_wait

        action = Action(
            action_type=ActionType.REMINDER,
            trigger_at=trigger_at,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            message_id=ctx.message.id,
            extra={"content": text},
        )

        action = await self.bot.actions.schedule(action)
        when = timestamp(action.trigger_at)

        identifier = ""
        if action.id:
            identifier = f" (`#{action.id}`)"

        await ctx.send(f":ok_hand: I will remind you about this on **{when:F}**" + identifier)

    @reminder.command(name="list", aliases=["ls"])
    async def reminder_list(self, ctx: TomodachiContext, help="Prints all active reminders you have."):
        async with self.bot.db.pool.acquire() as conn:
            query = "SELECT * FROM actions WHERE author_id=$1 AND action_type='REMINDER' LIMIT 500;"
            stmt = await conn.prepare(query)
            rows = await stmt.fetch(ctx.author.id)

        actions = tuple(Action(**row) for row in rows)
        if not actions:
            return await ctx.send(":x: You don't have any reminders!")

        lines = []
        for reminder in actions:
            when = timestamp(reminder.trigger_at)
            line = f"**(#{reminder.id})** on {when:F}"
            lines.append(line)

        entries = ["\n".join(chunk) for chunk in miter.chunked(lines, 10)]

        menu = ctx.new_menu(entries)
        menu.embed.set_author(name=f"Requested by {ctx.author}", icon_url=helpers.avatar_or_default(ctx.author).url)

        await menu.start(ctx)

    @reminder.command(name="info", aliases=["check", "view"], help="Shows content of the specified reminder")
    async def reminder_info(self, ctx: TomodachiContext, reminder_id: EntryID):
        async with self.bot.db.pool.acquire() as conn:
            query = "SELECT * FROM actions WHERE author_id=$1 AND id=$2 AND action_type='REMINDER' LIMIT 1;"
            row = await conn.fetchrow(query, ctx.author.id, reminder_id)

        if not row:
            return await ctx.send(f":x: You don't have reminder with ID `#{reminder_id}`.")

        action = Action(**row)

        embed = discord.Embed()
        embed.title = f"Reminder #{action.id}"
        embed.description = f"{action.extra['content'][0:2000]}"
        embed.timestamp = action.trigger_at
        embed.set_footer(text=f"{ctx.author}", icon_url=helpers.avatar_or_default(ctx.author).url)

        await ctx.send(embed=embed, delete_after=120)

    @reminder.command(name="remove", aliases=["rmv", "delete", "del"], help="Remove some reminder from your list")
    async def reminder_remove(self, ctx: TomodachiContext, reminder_id: EntryID):
        async with self.bot.db.pool.acquire() as conn:
            query = "DELETE FROM actions WHERE author_id=$1 AND id=$2 AND action_type='REMINDER' RETURNING true;"
            value = await conn.fetchval(query, ctx.author.id, reminder_id)

        if not value:
            return await ctx.send(f":x: Nothing happened. Most likely you don't have a reminder `#{reminder_id}`.")

        await self.bot.actions.redispatch()
        await ctx.send(f":ok_hand: Successfully deleted `#{reminder_id}` reminder.")

    @reminder.command(name="purge", aliases=["clear"])
    async def reminder_purge(self, ctx: TomodachiContext):
        async with self.bot.db.pool.acquire() as conn:
            query = """WITH deleted AS (DELETE FROM actions WHERE author_id=$1 AND action_type='REMINDER' RETURNING *)
                SELECT count(*)
                FROM deleted;"""
            stmt = await conn.prepare(query)
            count: int = await stmt.fetchval(ctx.author.id)

        if not count:
            return await ctx.send(":x: Nothing happened. Looks like you have no reminders.")

        await self.bot.actions.redispatch()
        await ctx.send(f":ok_hand: Deleted `{count}` reminder(s) from your list.")


def setup(bot):
    bot.add_cog(Tools(bot))
