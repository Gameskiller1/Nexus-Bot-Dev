
# rayward_integration.py
import os
import time
import json
import asyncio
import logging
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import database

log = logging.getLogger("nexus.rayward")

RAYWARD_BASE_URL = os.getenv("RAYWARD_BASE_URL", "https://rayward.app")
RAYWARD_API_KEY = os.getenv("RAYWARD_API_KEY", "").strip()

if not RAYWARD_API_KEY:
    log.warning("RAYWARD_API_KEY is not set. Rayward commands will not work.")

# ============================================================
# DATABASE EXTENSIONS
# ============================================================

def init_rayward_tables():
    with database.transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rayward_settings (
                guild_id INTEGER PRIMARY KEY,
                alert_channel_id INTEGER,
                auto_alert INTEGER NOT NULL DEFAULT 1,
                min_flag_type INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rayward_flagged_cache (
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                flag_type INTEGER NOT NULL,
                category TEXT,
                status_label TEXT,
                reasons_json TEXT,
                source TEXT NOT NULL DEFAULT 'Rotector',
                request_id TEXT,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(subject_type, subject_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rayward_review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                actor_id INTEGER,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                note TEXT,
                request_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rayward_watchlist (
                guild_id INTEGER NOT NULL,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                added_by INTEGER,
                reason TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id, subject_type, subject_id)
            )
        """)

def set_rayward_setting(guild_id: int, alert_channel_id=None, auto_alert=None, min_flag_type=None):
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT guild_id, alert_channel_id, auto_alert, min_flag_type FROM rayward_settings WHERE guild_id=?",
            (guild_id,)
        ).fetchone()
        if row:
            alert_channel_id = row[1] if alert_channel_id is None else alert_channel_id
            auto_alert = row[2] if auto_alert is None else int(bool(auto_alert))
            min_flag_type = row[3] if min_flag_type is None else int(min_flag_type)
        conn.execute("""
            INSERT INTO rayward_settings (guild_id, alert_channel_id, auto_alert, min_flag_type, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                alert_channel_id=excluded.alert_channel_id,
                auto_alert=excluded.auto_alert,
                min_flag_type=excluded.min_flag_type,
                updated_at=CURRENT_TIMESTAMP
        """, (guild_id, alert_channel_id, int(bool(auto_alert)) if auto_alert is not None else 1, min_flag_type or 1))

def get_rayward_setting(guild_id: int):
    row = database._query(
        "SELECT alert_channel_id, auto_alert, min_flag_type FROM rayward_settings WHERE guild_id=?",
        (guild_id,),
        one=True
    )
    return row if row else (None, 1, 1)

def cache_rayward_result(subject_type: str, subject_id: str, data: dict):
    with database.transaction() as conn:
        conn.execute("""
            INSERT INTO rayward_flagged_cache
            (subject_type, subject_id, flag_type, category, status_label, reasons_json, source, request_id, last_checked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(subject_type, subject_id) DO UPDATE SET
                flag_type=excluded.flag_type,
                category=excluded.category,
                status_label=excluded.status_label,
                reasons_json=excluded.reasons_json,
                source=excluded.source,
                request_id=excluded.request_id,
                last_checked=CURRENT_TIMESTAMP
        """, (
            subject_type,
            str(subject_id),
            int(data.get("flagType", 0)),
            data.get("category"),
            data.get("statusLabel"),
            json.dumps(data.get("reasons", []), ensure_ascii=False),
            data.get("source", "Rotector"),
            data.get("requestId"),
        ))

def get_cached_rayward(subject_type: str, subject_id: str):
    row = database._query("""
        SELECT flag_type, category, status_label, reasons_json, source, request_id, last_checked
        FROM rayward_flagged_cache
        WHERE subject_type=? AND subject_id=?
    """, (subject_type, str(subject_id)), one=True)
    return row

def add_review_log(guild_id, actor_id, subject_type, subject_id, verdict, note=None, request_id=None):
    with database.transaction() as conn:
        conn.execute("""
            INSERT INTO rayward_review_log (guild_id, actor_id, subject_type, subject_id, verdict, note, request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, actor_id, subject_type, str(subject_id), verdict, note, request_id))

# ============================================================
# API
# ============================================================

async def rayward_request(session: aiohttp.ClientSession, method: str, path: str, payload=None):
    url = RAYWARD_BASE_URL.rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {RAYWARD_API_KEY}",
        "Accept": "application/json",
    }
    async with session.request(method, url, headers=headers, json=payload) as resp:
        data = await resp.json(content_type=None)
        return resp.status, data, resp.headers

def _flagged(flag_type: int):
    return flag_type in (1, 2)

def _summarize_reasons(reasons):
    lines = []
    for reason in reasons or []:
        title = reason.get("title") or reason.get("type", "reason")
        rtype = reason.get("type", "unknown")
        sources = ", ".join(s.get("id", "?") for s in reason.get("sources", []))
        lines.append(f"• {title} (`{rtype}`) [{sources}]")
    return lines or ["No reason details returned."]

def _make_embed(title: str, data: dict, subject_label: str):
    flag_type = int(data.get("flagType", 0))
    status_label = data.get("statusLabel", "Unknown")
    category = data.get("category")
    color = discord.Color.red() if _flagged(flag_type) else discord.Color.green()
    if flag_type in (3, 4, 5, 6, 8, 10):
        color = discord.Color.orange()
    embed = discord.Embed(title=title, color=color)
    embed.add_field(name="Subject", value=subject_label, inline=False)
    embed.add_field(name="Flag Type", value=f"{flag_type} ({status_label})", inline=True)
    if category:
        embed.add_field(name="Category", value=str(category), inline=True)
    if data.get("requestId"):
        embed.set_footer(text=f"Rotector request: {data['requestId']}")
    reasons = data.get("reasons", [])
    if reasons:
        embed.add_field(name="Reasons", value="\n".join(_summarize_reasons(reasons))[:1024], inline=False)
    else:
        embed.add_field(name="Reasons", value="None returned.", inline=False)
    return embed

async def lookup_user(session, user_id: str, exclude_info=False, lang="en"):
    path = f"/v2/lookup/rotector/roblox/user/{user_id}"
    if exclude_info:
        path += "?excludeInfo=true"
    if lang:
        path += ("&" if "?" in path else "?") + f"lang={lang}"
    return await rayward_request(session, "GET", path)

async def lookup_group(session, group_id: str, exclude_info=False, lang="en"):
    path = f"/v2/lookup/rotector/roblox/group/{group_id}"
    if exclude_info:
        path += "?excludeInfo=true"
    if lang:
        path += ("&" if "?" in path else "?") + f"lang={lang}"
    return await rayward_request(session, "GET", path)

async def lookup_discord(session, user_id: str, exclude_info=False, lang="en"):
    path = f"/v2/lookup/rotector/discord/user/{user_id}"
    if exclude_info:
        path += "?excludeInfo=true"
    if lang:
        path += ("&" if "?" in path else "?") + f"lang={lang}"
    return await rayward_request(session, "GET", path)

async def get_providers(session):
    return await rayward_request(session, "GET", "/v2/providers")

# ============================================================
# UI
# ============================================================

class RaywardPanel(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=180)
        self.cog = cog

    @discord.ui.button(label="Check User", style=discord.ButtonStyle.primary)
    async def check_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(UserLookupModal(self.cog))

    @discord.ui.button(label="Check Group", style=discord.ButtonStyle.primary)
    async def check_group(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GroupLookupModal(self.cog))

    @discord.ui.button(label="Check Discord", style=discord.ButtonStyle.secondary)
    async def check_discord(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DiscordLookupModal(self.cog))

    @discord.ui.button(label="Providers", style=discord.ButtonStyle.secondary)
    async def providers(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_providers(interaction)

class UserLookupModal(discord.ui.Modal, title="Rayward Roblox User Lookup"):
    user_id = discord.ui.TextInput(label="Roblox User ID", placeholder="123456789", required=True)
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.run_user_lookup(interaction, self.user_id.value)

class GroupLookupModal(discord.ui.Modal, title="Rayward Roblox Group Lookup"):
    group_id = discord.ui.TextInput(label="Roblox Group ID", placeholder="1234567", required=True)
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.run_group_lookup(interaction, self.group_id.value)

class DiscordLookupModal(discord.ui.Modal, title="Rayward Discord User Lookup"):
    discord_id = discord.ui.TextInput(label="Discord User ID", placeholder="123456789012345678", required=True)
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.run_discord_lookup(interaction, self.discord_id.value)

# ============================================================
# COG
# ============================================================

class RaywardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    async def run_user_lookup(self, interaction, user_id: str, store=True):
        await interaction.response.defer(ephemeral=True)
        session = self.session

    async def run_group_lookup(self, interaction, group_id: str, store=True):
        await interaction.response.defer(ephemeral=True)
        session = self.session

    async def run_discord_lookup(self, interaction, discord_id: str, store=True):
        await interaction.response.defer(ephemeral=True)
        session = self.session

    async def send_providers(self, interaction):
        await interaction.response.defer(ephemeral=True)
        status, data, _ = await get_providers(self.session)
        if status != 200:
            await interaction.followup.send(f"Failed to fetch providers: `{status}`", ephemeral=True)
            return
        lines = []
        for src in data.get("sources", data if isinstance(data, list) else []):
            lines.append(f"• {src.get('id', 'unknown')} - {src.get('label', 'unknown')}")
        embed = discord.Embed(title="Rayward Providers", description="\n".join(lines)[:4000], color=discord.Color.blurple())
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="rayward-panel", description="Open the Rayward staff panel")
    async def rayward_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Rayward moderation panel:",
            view=RaywardPanel(self),
            ephemeral=True
        )

    @app_commands.command(name="rayward-user", description="Check a Roblox user in Rayward")
    @app_commands.describe(user_id="Roblox user ID")
    async def rayward_user(self, interaction: discord.Interaction, user_id: str):
        await self.run_user_lookup(interaction, user_id)

    @app_commands.command(name="rayward-group", description="Check a Roblox group in Rayward")
    @app_commands.describe(group_id="Roblox group ID")
    async def rayward_group(self, interaction: discord.Interaction, group_id: str):
        await self.run_group_lookup(interaction, group_id)

    @app_commands.command(name="rayward-discord", description="Check a Discord user in Rayward")
    @app_commands.describe(user_id="Discord user ID")
    async def rayward_discord(self, interaction: discord.Interaction, user_id: str):
        await self.run_discord_lookup(interaction, user_id)

    @app_commands.command(name="rayward-providers", description="Show enabled Rayward providers")
    async def rayward_providers(self, interaction: discord.Interaction):
        await self.send_providers(interaction)

    @app_commands.command(name="rayward-joinwatch-set", description="Set the channel used for join alerts")
    @app_commands.describe(channel="Channel for alerts")
    async def rayward_joinwatch_set(self, interaction: discord.Interaction, channel: discord.TextChannel):
        set_rayward_setting(interaction.guild.id, alert_channel_id=channel.id)
        await interaction.response.send_message(f"✅ Join watch channel set to {channel.mention}", ephemeral=True)

    @app_commands.command(name="rayward-joinwatch-disable", description="Disable auto alerts")
    async def rayward_joinwatch_disable(self, interaction: discord.Interaction):
        set_rayward_setting(interaction.guild.id, auto_alert=0)
        await interaction.response.send_message("✅ Join alerts disabled.", ephemeral=True)

    @app_commands.command(name="rayward-joinwatch-enable", description="Enable auto alerts")
    async def rayward_joinwatch_enable(self, interaction: discord.Interaction):
        set_rayward_setting(interaction.guild.id, auto_alert=1)
        await interaction.response.send_message("✅ Join alerts enabled.", ephemeral=True)

    @app_commands.command(name="rayward-joinwatch-status", description="Show join watch settings")
    async def rayward_joinwatch_status(self, interaction: discord.Interaction):
        ch, auto, min_flag = get_rayward_setting(interaction.guild.id)
        text = f"Channel: {f'<#{ch}>' if ch else 'Not set'}\nAuto alerts: {'On' if auto else 'Off'}\nMin flag type: {min_flag}"
        await interaction.response.send_message(text, ephemeral=True)

    async def _report_if_flagged(self, guild, member, subject_type, subject_id, data):
        flag_type = int(data.get("flagType", 0))
        if not _flagged(flag_type):
            return
        ch_id, auto_alert, min_flag = get_rayward_setting(guild.id)
        if not auto_alert or flag_type < min_flag or not ch_id:
            return
        channel = guild.get_channel(int(ch_id))
        if not channel:
            return
        embed = _make_embed("Rayward Flag Alert", data, f"{subject_type}: {subject_id}")
        embed.add_field(name="Member", value=f"{member.mention if member else subject_id}", inline=False)
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        session = self.session

def setup(bot):
    bot.add_cog(RaywardCog(bot))
