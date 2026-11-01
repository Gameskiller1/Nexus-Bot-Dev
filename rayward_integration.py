import os
import time
import logging
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import database

log = logging.getLogger("nexus.rayward")

RAYWARD_BASE_URL = os.getenv("RAYWARD_BASE_URL", "https://rayward.app").rstrip("/")
RAYWARD_API_KEY = os.getenv("RAYWARD_API_KEY", "").strip()

# Stored locally only for flagged results / configuration
# NOTE: Rayward responses must not be stored longer than 24h. This module only stores
# minimal metadata, and only for positive hits or operational config.


def init_rayward_tables():
    with database.transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rayward_settings (
                guild_id INTEGER NOT NULL,
                setting_key TEXT NOT NULL,
                setting_value TEXT NOT NULL,
                PRIMARY KEY (guild_id, setting_key)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rayward_flag_cache (
                guild_id INTEGER NOT NULL,
                subject_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                flag_type INTEGER NOT NULL,
                category TEXT,
                reason_summary TEXT,
                checked_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                source_request_id TEXT,
                PRIMARY KEY (guild_id, subject_type, subject_id)
            )
        """)

rayward_group = app_commands.Group(
    name="rayward",
    description="Rayward / Rotector moderation tools"
)

def _guild_id(interaction_or_member) -> int:
    guild = getattr(interaction_or_member, "guild", None)
    if guild is None:
        return 0
    return guild.id


def _get_setting(guild_id: int, key: str, default=None):
    return database.get_config(f"rayward:{guild_id}:{key}", default)


def _set_setting(guild_id: int, key: str, value: str):
    database.set_config(f"rayward:{guild_id}:{key}", value)


def _clear_setting(guild_id: int, key: str):
    database.set_config(f"rayward:{guild_id}:{key}", "")


def _has_access_role(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    role_id = _get_setting(member.guild.id, "access_role_id", "")
    if not role_id:
        return False
    return any(r.id == int(role_id) for r in member.roles)


def rayward_access_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        return _has_access_role(interaction.user)
    return app_commands.check(predicate)


def _parse_reason_summary(payload: dict) -> str:
    reasons = payload.get("reasons") or []
    if not reasons:
        return ""
    parts = []
    for r in reasons[:3]:
        title = r.get("title") or r.get("type") or "reason"
        evidence = r.get("evidence") or []
        snippet = []
        for ev in evidence[:2]:
            if ev.get("kind") == "text" and ev.get("text"):
                snippet.append(ev["text"])
        if snippet:
            parts.append(f"{title}: {'; '.join(snippet)}")
        else:
            parts.append(str(title))
    return " | ".join(parts)[:900]


def _is_actionable_flag(flag_type: int) -> bool:
    return flag_type in (1, 2)


async def _api_request(path: str, *, method="GET", json_body=None):
    if not RAYWARD_API_KEY:
        return None, {"success": False, "error": "Rayward API key missing in environment", "code": "NO_KEY"}
    headers = {
        "Authorization": f"Bearer {RAYWARD_API_KEY}",
        "Accept": "application/json",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.request(method, f"{RAYWARD_BASE_URL}{path}", json=json_body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = {"success": False, "error": f"Non-JSON response: HTTP {resp.status}"}
            data["_http_status"] = resp.status
            data["_headers"] = dict(resp.headers)
            return resp.status, data


async def _lookup_user(user_id: str, *, exclude_info=True, lang="en"):
    q = f"?excludeInfo={'true' if exclude_info else 'false'}&lang={lang}"
    return await _api_request(f"/v2/lookup/rotector/roblox/user/{user_id}{q}")


async def _lookup_group(group_id: str, *, exclude_info=True, lang="en"):
    q = f"?excludeInfo={'true' if exclude_info else 'false'}&lang={lang}"
    return await _api_request(f"/v2/lookup/rotector/roblox/group/{group_id}{q}")


async def _lookup_discord(discord_id: str, *, exclude_info=True, lang="en"):
    q = f"?excludeInfo={'true' if exclude_info else 'false'}&lang={lang}"
    return await _api_request(f"/v2/lookup/rotector/discord/user/{discord_id}{q}")


async def _lookup_user_discords(user_id: str):
    return await _api_request(f"/v2/lookup/rotector/roblox/user/{user_id}/discord?lang=en")


async def _providers():
    return await _api_request("/v2/providers?lang=en")


def _store_hit(guild_id: int, subject_type: str, subject_id: str, payload: dict):
    flag_type = int(payload.get("flagType", 0) or 0)
    if not _is_actionable_flag(flag_type):
        return
    category = payload.get("category")
    summary = _parse_reason_summary(payload)
    request_id = payload.get("requestId")
    now = int(time.time())
    expires = now + (23 * 60 * 60)
    with database.transaction() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO rayward_flag_cache
            (guild_id, subject_type, subject_id, flag_type, category, reason_summary, checked_at, expires_at, source_request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, subject_type, subject_id, flag_type, category, summary, now, expires, request_id))


def _get_cached_hit(guild_id: int, subject_type: str, subject_id: str):
    row = database._query(
        "SELECT flag_type, category, reason_summary, checked_at, expires_at, source_request_id "
        "FROM rayward_flag_cache WHERE guild_id=? AND subject_type=? AND subject_id=?",
        (guild_id, subject_type, subject_id),
        one=True,
    )
    if not row:
        return None
    if int(row[4]) <= int(time.time()):
        with database.transaction() as conn:
            conn.execute(
                "DELETE FROM rayward_flag_cache WHERE guild_id=? AND subject_type=? AND subject_id=?",
                (guild_id, subject_type, subject_id),
            )
        return None
    return {
        "flag_type": row[0],
        "category": row[1],
        "reason_summary": row[2],
        "checked_at": row[3],
        "expires_at": row[4],
        "request_id": row[5],
    }


def _fmt_flag(payload: dict) -> str:
    flag_type = int(payload.get("flagType", 0) or 0)
    status = payload.get("statusLabel") or "Unknown"
    category = payload.get("category")
    if flag_type == 0:
        return f"Unflagged ({status})"
    if category:
        return f"{status} | category: {category}"
    return status


def _embed_from_payload(subject_title: str, subject_value: str, payload: dict, source_label: str):
    flag_type = int(payload.get("flagType", 0) or 0)
    color = discord.Color.red() if _is_actionable_flag(flag_type) else discord.Color.orange() if flag_type else discord.Color.green()
    embed = discord.Embed(
        title=f"Rayward Check: {subject_title}",
        color=color
    )
    embed.add_field(name="Subject", value=subject_value, inline=False)
    embed.add_field(name="Source", value=f"Rotector via Rayward", inline=True)
    embed.add_field(name="Verdict", value=_fmt_flag(payload), inline=True)
    embed.add_field(name="Flag Type", value=str(flag_type), inline=True)

    if payload.get("category"):
        embed.add_field(name="Category", value=str(payload["category"]), inline=True)

    reasons = payload.get("reasons") or []
    if reasons:
        lines = []
        for r in reasons[:5]:
            title = r.get("title") or r.get("type") or "Reason"
            rtype = r.get("type") or "unknown"
            lines.append(f"• **{title}** (`{rtype}`)")
        embed.add_field(name="Reasons", value="\n".join(lines)[:1024], inline=False)
        if reasons[0].get("evidence"):
            ev_lines = []
            for ev in reasons[0]["evidence"][:5]:
                if ev.get("kind") == "text" and ev.get("text"):
                    ev_lines.append(f"• {ev['text']}")
                elif ev.get("kind") == "discordUser" and ev.get("discordId"):
                    ev_lines.append(f"• Discord user: `{ev['discordId']}`")
                elif ev.get("kind") == "discordGuild" and ev.get("discordId"):
                    ev_lines.append(f"• Discord guild: `{ev['discordId']}`")
            if ev_lines:
                embed.add_field(name="Evidence", value="\n".join(ev_lines)[:1024], inline=False)

    embed.set_footer(text="Rotector data may change. Review manually before acting.")
    return embed


class RaywardPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Check User", style=discord.ButtonStyle.primary)
    async def user_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/rayward user <roblox_id>`", ephemeral=True)

    @discord.ui.button(label="Check Group", style=discord.ButtonStyle.primary)
    async def group_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/rayward group <group_id>`", ephemeral=True)

    @discord.ui.button(label="Check Discord", style=discord.ButtonStyle.primary)
    async def discord_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/rayward discord <discord_id>`", ephemeral=True)

    @discord.ui.button(label="Set Access Role", style=discord.ButtonStyle.secondary)
    async def role_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/rayward access-role set <role>`", ephemeral=True)

    @discord.ui.button(label="Set Alert Channel", style=discord.ButtonStyle.secondary)
    async def channel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/rayward alerts-channel set <channel>`", ephemeral=True)


class RaywardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @rayward_group.command(name="panel", description="Open the Rayward staff panel")
    @rayward_access_check()
    async def panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Rayward Staff Panel",
            description=(
                "Use the buttons for quick hints, or run the slash commands directly.\n\n"
                "**Available:**\n"
                "• `/rayward user`\n"
                "• `/rayward group`\n"
                "• `/rayward discord`\n"
                "• `/rayward access-role set`\n"
                "• `/rayward alerts-channel set`\n"
                "• `/rayward providers`\n"
            ),
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, view=RaywardPanelView(), ephemeral=True)

    @rayward_group.command(name="access-role-set", description="Set the role allowed to use Rayward commands")
    @app_commands.describe(role="Role that may use Rayward commands")
    @rayward_access_check()
    async def access_role_set(self, interaction: discord.Interaction, role: discord.Role):
        _set_setting(interaction.guild.id, "access_role_id", str(role.id))
        await interaction.response.send_message(f"✅ Rayward access role set to {role.mention}", ephemeral=True)

    @rayward_group.command(name="access-role-clear", description="Clear the Rayward access role")
    @rayward_access_check()
    async def access_role_clear(self, interaction: discord.Interaction):
        _set_setting(interaction.guild.id, "access_role_id", "")
        await interaction.response.send_message("✅ Rayward access role cleared", ephemeral=True)

    @rayward_group.command(name="access-role-view", description="View the Rayward access role")
    @rayward_access_check()
    async def access_role_view(self, interaction: discord.Interaction):
        rid = _get_setting(interaction.guild.id, "access_role_id", "")
        if not rid:
            await interaction.response.send_message("No access role set.", ephemeral=True)
            return
        role = interaction.guild.get_role(int(rid))
        await interaction.response.send_message(
            f"Current Rayward access role: {role.mention if role else f'Unknown role ({rid})'}",
            ephemeral=True
        )

    @rayward_group.command(name="alerts-channel-set", description="Set the channel where flagged joins are posted")
    @app_commands.describe(channel="Channel for join alerts")
    @rayward_access_check()
    async def alerts_channel_set(self, interaction: discord.Interaction, channel: discord.TextChannel):
        _set_setting(interaction.guild.id, "alerts_channel_id", str(channel.id))
        await interaction.response.send_message(f"✅ Join alert channel set to {channel.mention}", ephemeral=True)

    @rayward_group.command(name="alerts-channel-clear", description="Clear the join alert channel")
    @rayward_access_check()
    async def alerts_channel_clear(self, interaction: discord.Interaction):
        _set_setting(interaction.guild.id, "alerts_channel_id", "")
        await interaction.response.send_message("✅ Join alert channel cleared", ephemeral=True)

    @rayward_group.command(name="alerts-channel-view", description="View the join alert channel")
    @rayward_access_check()
    async def alerts_channel_view(self, interaction: discord.Interaction):
        cid = _get_setting(interaction.guild.id, "alerts_channel_id", "")
        if not cid:
            await interaction.response.send_message("No alert channel set.", ephemeral=True)
            return
        channel = interaction.guild.get_channel(int(cid))
        await interaction.response.send_message(
            f"Current alert channel: {channel.mention if channel else f'Unknown channel ({cid})'}",
            ephemeral=True
        )

    @rayward_group.command(name="user", description="Check a Roblox user ID against Rotector")
    @app_commands.describe(user_id="Roblox user ID", exclude_info="Hide evidence/details")
    @rayward_access_check()
    async def user_lookup(self, interaction: discord.Interaction, user_id: str, exclude_info: bool = True):
        await interaction.response.defer(ephemeral=True)
        status, data = await _lookup_user(user_id, exclude_info=exclude_info)
        if not data or not data.get("success", False):
            msg = data.get("error", "Lookup failed") if isinstance(data, dict) else "Lookup failed"
            await interaction.followup.send(f"❌ Rayward error: {msg}", ephemeral=True)
            return
        _store_hit(interaction.guild.id, "roblox_user", str(user_id), data)
        embed = _embed_from_payload("Roblox User", str(user_id), data, "Rotector")
        if int(data.get("flagType", 0) or 0) == 0:
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(content="Rotector source used. Review manually before acting.", embed=embed, ephemeral=True)

    @rayward_group.command(name="group", description="Check a Roblox group ID against Rotector")
    @app_commands.describe(group_id="Roblox group ID", exclude_info="Hide evidence/details")
    @rayward_access_check()
    async def group_lookup(self, interaction: discord.Interaction, group_id: str, exclude_info: bool = True):
        await interaction.response.defer(ephemeral=True)
        status, data = await _lookup_group(group_id, exclude_info=exclude_info)
        if not data or not data.get("success", False):
            msg = data.get("error", "Lookup failed") if isinstance(data, dict) else "Lookup failed"
            await interaction.followup.send(f"❌ Rayward error: {msg}", ephemeral=True)
            return
        _store_hit(interaction.guild.id, "roblox_group", str(group_id), data)
        embed = _embed_from_payload("Roblox Group", str(group_id), data, "Rotector")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @rayward_group.command(name="discord", description="Check a Discord user ID against Rotector")
    @app_commands.describe(discord_id="Discord snowflake ID", exclude_info="Hide evidence/details")
    @rayward_access_check()
    async def discord_lookup(self, interaction: discord.Interaction, discord_id: str, exclude_info: bool = True):
        await interaction.response.defer(ephemeral=True)
        status, data = await _lookup_discord(discord_id, exclude_info=exclude_info)
        if not data or not data.get("success", False):
            msg = data.get("error", "Lookup failed") if isinstance(data, dict) else "Lookup failed"
            await interaction.followup.send(f"❌ Rayward error: {msg}", ephemeral=True)
            return
        _store_hit(interaction.guild.id, "discord_user", str(discord_id), data)
        embed = _embed_from_payload("Discord User", str(discord_id), data, "Rotector")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @rayward_group.command(name="providers", description="List Rayward enabled sources")
    @rayward_access_check()
    async def providers(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        status, data = await _providers()
        if not data or not data.get("success", False):
            msg = data.get("error", "Could not load providers") if isinstance(data, dict) else "Could not load providers"
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return
        providers = data.get("providers") or data.get("sources") or []
        if isinstance(providers, list):
            lines = []
            for p in providers[:30]:
                if isinstance(p, dict):
                    name = p.get("name") or p.get("id") or "unknown"
                    summary = p.get("summary") or p.get("label") or ""
                    lines.append(f"• **{name}** {('- ' + summary) if summary else ''}")
                else:
                    lines.append(f"• {p}")
            txt = "\n".join(lines) if lines else "No provider details returned."
        else:
            txt = str(providers)
        embed = discord.Embed(title="Rayward Providers", description=txt[:4000], color=discord.Color.blurple())
        await interaction.followup.send(embed=embed, ephemeral=True)

    @rayward_group.command(name="cached", description="Show cached flagged results for a subject")
    @app_commands.describe(subject_type="roblox_user, roblox_group, or discord_user", subject_id="ID to inspect")
    @rayward_access_check()
    async def cached(self, interaction: discord.Interaction, subject_type: str, subject_id: str):
        hit = _get_cached_hit(interaction.guild.id, subject_type, subject_id)
        if not hit:
            await interaction.response.send_message("No cached flagged result found (or it expired).", ephemeral=True)
            return
        embed = discord.Embed(title="Cached Rayward Flag", color=discord.Color.red())
        embed.add_field(name="Subject Type", value=subject_type, inline=True)
        embed.add_field(name="Subject ID", value=subject_id, inline=True)
        embed.add_field(name="Flag Type", value=str(hit["flag_type"]), inline=True)
        if hit.get("category"):
            embed.add_field(name="Category", value=hit["category"], inline=True)
        if hit.get("reason_summary"):
            embed.add_field(name="Summary", value=hit["reason_summary"][:1024], inline=False)
        embed.add_field(name="Cached At", value=str(hit["checked_at"]), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or not member.guild:
            return
        channel_id = _get_setting(member.guild.id, "alerts_channel_id", "")
        if not channel_id:
            return
        # Prefer linked Discord ID check first.
        _, data = await _lookup_discord(str(member.id), exclude_info=True)
        if not data or not data.get("success", False):
            return
        flag_type = int(data.get("flagType", 0) or 0)
        if not _is_actionable_flag(flag_type):
            return
        _store_hit(member.guild.id, "discord_user", str(member.id), data)
        channel = member.guild.get_channel(int(channel_id))
        if not channel:
            return
        embed = _embed_from_payload("Joined Discord User", f"{member.mention} (`{member.id}`)", data, "Rotector")
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(content=f"⚠️ Flagged user joined: {member.mention}", embed=embed)
        except discord.Forbidden:
            pass


async def _on_app_command_error(interaction: discord.Interaction, error: Exception):
    if isinstance(error, app_commands.CheckFailure):
        msg = "⛔ You do not have the required Rayward role."
    else:
        log.exception("Rayward command failed", exc_info=error)
        msg = f"⚠️ Rayward command failed: `{type(error).__name__}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


def setup(bot: commands.Bot):
    if not bot.tree.on_error or bot.tree.on_error is None:
        bot.tree.on_error = _on_app_command_error
    bot.add_cog(RaywardCog(bot))


