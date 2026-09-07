import os
import re
import logging
import logging.handlers

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import database

# ============================================================
# LOGGING
# ============================================================
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "nexus.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler()  # -> journalctl
_console_handler.setFormatter(_fmt)

_root = logging.getLogger()
_root.setLevel(logging.INFO)
if not _root.handlers:
    _root.addHandler(_file_handler)
    _root.addHandler(_console_handler)
logging.getLogger("discord").setLevel(logging.WARNING)

log = logging.getLogger("nexus.bot")

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN missing from .env")

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ============================================================
# DATABASE INIT
# ============================================================
database.init_db()
database.init_role_tags_table()
database.init_ranking_table()
database.init_demotion_table()
database.init_user_ranks_table()
database.init_startup_roles_table()
database.init_award_history_table()
database.init_config_table()
database.init_np_log()
database.run_migrations()
log.info("Database ready: %s", database.DB_FILE)

# ============================================================
# GALAXY THEME CONFIG
# ============================================================
GALAXY_GIF_URL = "https://c.tenor.com/Eh29GgC7YqEAAAAd/tenor.gif"

def apply_galaxy_theme(embed: discord.Embed) -> discord.Embed:
    """Attach the animated galaxy background image to an embed."""
    embed.set_image(url=GALAXY_GIF_URL)
    return embed

# ============================================================
# CONFIG
# ============================================================
WELCOME_CHANNEL_ID = None  # Set to your welcome channel ID for join messages


def is_mod():
    """Restrict command to users with Manage Server permission."""
    def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.manage_guild
    return app_commands.check(predicate)


TAG_PATTERN = re.compile(r"^(\[.*?\])+\s*")


def strip_tags(name: str) -> str:
    """Remove any existing [tag] prefixes from a name."""
    return TAG_PATTERN.sub("", name).strip()


async def sync_member_tags(member: discord.Member):
    """Rebuild a member's nickname based on tags attached to their roles."""
    if member.bot:
        return

    role_tags = dict(database.get_all_role_tags())
    member_roles_sorted = sorted(member.roles, key=lambda r: r.position, reverse=True)
    tags = [role_tags[r.id] for r in member_roles_sorted if r.id in role_tags]

    base_name = strip_tags(member.nick or member.display_name)
    new_nick = f"{''.join(tags)} {base_name}" if tags else base_name
    new_nick = new_nick[:32]

    if member.nick != new_nick and new_nick != member.name:
        try:
            await member.edit(nick=new_nick)
        except discord.Forbidden:
            log.warning("Could not edit nickname for %s", member.id)


@bot.event
async def on_ready():
    await bot.tree.sync()
    log.info("Logged in as %s - slash commands synced.", bot.user)
    if not auto_rank_members.is_running():
        auto_rank_members.start()


@bot.event
async def on_member_join(member: discord.Member):
    """Assign startup roles when member joins."""
    if member.bot:
        return

    database.ensure_user(member.id)

    startup_roles = database.get_startup_roles()
    if startup_roles:
        roles_to_add = [member.guild.get_role(role_id) for role_id in startup_roles]
        roles_to_add = [r for r in roles_to_add if r is not None]
        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add)
            except discord.Forbidden:
                log.warning("Could not assign startup roles to %s", member.id)

    if WELCOME_CHANNEL_ID:
        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title=f"Welcome, {member.name}!",
                description=f"{member.mention} has joined the server!",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            apply_galaxy_theme(embed)
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                log.warning("Could not send welcome message for %s", member.id)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles != after.roles:
        await sync_member_tags(after)


@tasks.loop(minutes=1)
async def auto_rank_members():
    """Auto-promote members who qualify. Never demotes."""
    try:
        np_map = database.get_all_np()
        ranks = database.get_ranks()
        rank_cache = {r[0]: r for r in ranks}
    except Exception:
        log.exception("auto_rank: could not load data")
        return

    for guild in bot.guilds:
        me = guild.me or guild.get_member(bot.user.id)
        for member in guild.members:
            if member.bot:
                continue

            if database.is_promo_locked(member.id):
                continue

            np_amount = np_map.get(member.id, 0)
            current_rank_id = database.get_user_rank(member.id)
            current_rank_info = rank_cache.get(current_rank_id) if current_rank_id else None

            # Manual-only ranks sit outside the NP ladder
            if current_rank_info and not current_rank_info[4]:
                continue

            rank_info = database.get_appropriate_rank(np_amount, auto_only=True)
            if not rank_info:
                continue

            rank_id, rank_name, role_id, obtainable = rank_info
            if current_rank_id == rank_id:
                continue

            target = rank_cache.get(rank_id)
            if not target:
                continue

            new_threshold = target[2]
            current_threshold = current_rank_info[2] if current_rank_info else -1
            if new_threshold <= current_threshold:
                continue  # never auto-demote

            database.set_user_rank(member.id, rank_id)
            log.info("AUTO-RANK %s (%s) -> %s at %s NP",
                     member.id, member.display_name, rank_name, np_amount)

            for rank_data in ranks:
                old_role = guild.get_role(rank_data[3])
                if old_role and old_role in member.roles:
                    try:
                        await member.remove_roles(old_role, reason="Auto-rank")
                    except discord.Forbidden:
                        log.warning("Cannot remove role %s from %s", old_role.id, member.id)

            new_role = guild.get_role(role_id)
            if new_role and new_role not in member.roles:
                try:
                    await member.add_roles(new_role, reason="Auto-rank")
                except discord.Forbidden:
                    log.warning("Cannot add role %s to %s", role_id, member.id)

            await sync_member_tags(member)

            try:
                embed = discord.Embed(
                    title="🎉 Rank Up!",
                    description=f"{member.mention} advanced to **{rank_name}**!",
                    color=discord.Color.gold()
                )
                apply_galaxy_theme(embed)

                autopromo_channel_id = database.get_config("autopromo_channel_id")
                if autopromo_channel_id:
                    channel = guild.get_channel(int(autopromo_channel_id))
                    if channel and me and channel.permissions_for(me).send_messages:
                        await channel.send(embed=embed)
                else:
                    for channel in guild.text_channels:
                        if me and channel.permissions_for(me).send_messages:
                            await channel.send(embed=embed)
                            break
            except Exception:
                log.exception("Rank-up announcement failed for %s", member.id)


@auto_rank_members.error
async def auto_rank_error(error):
    log.exception("auto_rank_members crashed", exc_info=error)


rank_group = app_commands.Group(name="rank", description="Manage ranks and auto-ranking")
startup_group = app_commands.Group(name="startup-role", description="Manage roles assigned on join")
award_group = app_commands.Group(name="award", description="Give role-based awards")
config_group = app_commands.Group(name="config", description="Configure bot behavior")
tag_group = app_commands.Group(name="tag", description="Manage automatic role tags")

# ============================================================
# NP COMMANDS
# ============================================================

@bot.tree.command(name="np", description="Give Nexus Points to a user")
@app_commands.describe(user="The user to give NP to", amount="Amount of NP to give",
                       reason="Why (recorded in the audit log)")
@is_mod()
async def np(interaction: discord.Interaction, user: discord.Member,
             amount: app_commands.Range[int, 1, 100000], reason: str = None):
    new_balance = database.add_np(user.id, amount, actor_id=interaction.user.id,
                                  source="/np", note=reason)
    embed = discord.Embed(
        title="Nexus Points Awarded",
        description=f"{user.mention} received **{amount} NP**.\nNew balance: **{new_balance} NP**",
        color=discord.Color.green()
    )
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="removenp", description="Remove Nexus Points from a user")
@app_commands.describe(user="The user to remove NP from", amount="Amount of NP to remove (max 1000)",
                       reason="Why (recorded in the audit log)")
@is_mod()
async def removenp(interaction: discord.Interaction, user: discord.Member,
                   amount: app_commands.Range[int, 1, 1000], reason: str = None):
    before = database.get_np(user.id)
    new_balance = database.remove_np(user.id, amount, actor_id=interaction.user.id,
                                     source="/removenp", note=reason)
    embed = discord.Embed(
        title="Nexus Points Removed",
        description=f"Removed **{amount} NP** from {user.mention}.\n{before} -> **{new_balance} NP**",
        color=discord.Color.red()
    )
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="stats", description="Check your or another user's stats")
@app_commands.describe(user="(Optional) check another user's stats")
async def stats(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    balance = database.get_np(target.id)
    current_rank_id = database.get_user_rank(target.id)
    awards = database.get_user_awards(target.id)

    rank_name = "None"
    if current_rank_id:
        rank_data = database.get_rank_by_id(current_rank_id)
        if rank_data:
            rank_name = rank_data[1]

    if database.is_promo_locked(target.id):
        rank_name += " 🔒"

    award_lines = []
    for role_id, np_bonus, awarded_at in awards:
        role = interaction.guild.get_role(role_id)
        role_name = role.mention if role else f"Unknown Role ({role_id})"
        bonus_text = f" | +{np_bonus} NP" if np_bonus else ""
        award_lines.append(f"• {role_name}{bonus_text}")

    award_str = "\n".join(award_lines) if award_lines else "None yet"

    embed = discord.Embed(
        title=f"{target.display_name}'s Profile",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Nexus Points", value=f"**{balance} NP**", inline=False)
    embed.add_field(name="Current Rank", value=rank_name, inline=False)
    embed.add_field(name="Awards", value=award_str, inline=False)
    embed.set_thumbnail(url=target.display_avatar.url)
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leaderboard", description="Show the Nexus Points leaderboard")
async def leaderboard(interaction: discord.Interaction):
    top = database.get_leaderboard(10)
    if not top:
        await interaction.response.send_message("No data yet.")
        return

    lines = []
    for i, (user_id, np_amount) in enumerate(top, start=1):
        member = interaction.guild.get_member(user_id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(user_id)
            except discord.NotFound:
                member = None
        name = member.display_name if member else f"Unknown User ({user_id})"
        lines.append(f"**#{i}** - {name}: **{np_amount} NP**")

    embed = discord.Embed(
        title="🏆 Nexus Points Leaderboard",
        description="\n".join(lines),
        color=discord.Color.gold()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed)


# ============================================================
# RANK COMMANDS
# ============================================================

@rank_group.command(name="add", description="Create a new rank milestone")
@app_commands.describe(
    name="Rank name",
    np_threshold="NP required to reach this rank",
    role="Role to assign at this rank",
    obtainable="Can this rank be obtained via auto-promotion? (True for auto, False for manual/applications)"
)
@is_mod()
async def rank_add(interaction: discord.Interaction, name: str,
                   np_threshold: app_commands.Range[int, 0, 1000000],
                   role: discord.Role, obtainable: bool = True):
    database.add_rank(name, np_threshold, role.id, obtainable=obtainable)
    ranks = database.get_ranks()
    created = next((r for r in ranks if r[1] == name), None)

    rank_type = "🟢 Auto" if obtainable else "🔴 Manual"
    embed = discord.Embed(
        title="✅ Rank Created",
        description=f"**{name}** requires **{np_threshold} NP** and grants {role.mention}\n"
                    f"Type: {rank_type}\n"
                    f"Rank ID: **{created[0] if created else 'N/A'}**",
        color=discord.Color.green()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@rank_group.command(name="list", description="Show all ranks")
async def rank_list(interaction: discord.Interaction):
    ranks = database.get_ranks()
    if not ranks:
        await interaction.response.send_message("No ranks configured.", ephemeral=True)
        return

    lines = []
    for rank_id, name, threshold, role_id, obtainable in ranks:
        role = interaction.guild.get_role(role_id)
        role_mention = role.mention if role else f"Unknown Role ({role_id})"
        rank_type = "🟢 Auto" if obtainable else "🔴 Manual"
        lines.append(f"**#{rank_id}** | **{name}** - {threshold} NP -> {role_mention} | {rank_type}")

    embed = discord.Embed(
        title="🏅 Rank System",
        description="\n".join(lines),
        color=discord.Color.blurple()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@rank_group.command(name="remove", description="Delete a rank")
@app_commands.describe(rank_id="The ID of the rank to remove")
@is_mod()
async def rank_remove(interaction: discord.Interaction, rank_id: app_commands.Range[int, 1, None]):
    rank_to_delete = database.get_rank_by_id(rank_id)

    if not rank_to_delete:
        await interaction.response.send_message(f"Rank with ID {rank_id} not found.", ephemeral=True)
        return

    database.delete_rank(rank_id)
    embed = discord.Embed(
        title="✅ Rank Deleted",
        description=f"Deleted rank **{rank_to_delete[1]}** (ID: {rank_id})",
        color=discord.Color.red()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@rank_group.command(name="promote", description="Manually promote a user to a specific rank")
@app_commands.describe(user="User to promote", rank_name="Target rank name")
@is_mod()
async def rank_promote(interaction: discord.Interaction, user: discord.Member, rank_name: str):
    await interaction.response.defer(ephemeral=False)

    target_rank = database.get_rank_by_name(rank_name)
    if not target_rank:
        await interaction.followup.send(
            f"❌ Rank **{rank_name}** not found. Use `/rank list` to see available ranks.",
            ephemeral=True
        )
        return

    target_rank_id, target_rank_name, target_threshold, target_role_id, target_obtainable = target_rank

    current_rank_id = database.get_user_rank(user.id)
    current_rank_data = database.get_rank_by_id(current_rank_id) if current_rank_id else None
    current_rank_name = current_rank_data[1] if current_rank_data else "None"
    current_threshold = current_rank_data[2] if current_rank_data else -1

    if target_threshold < current_threshold:
        embed = discord.Embed(
            title="❌ Cannot Demote",
            description=f"{user.mention} is already at **{current_rank_name}** "
                        f"(threshold: {current_threshold} NP)\n"
                        f"Cannot promote to a lower rank **{target_rank_name}** "
                        f"(threshold: {target_threshold} NP)",
            color=discord.Color.red()
        )
        apply_galaxy_theme(embed)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    database.set_user_rank(user.id, target_rank_id)

    for rank_data in database.get_ranks():
        old_role = interaction.guild.get_role(rank_data[3])
        if old_role and old_role in user.roles:
            try:
                await user.remove_roles(old_role, reason=f"Promotion by {interaction.user}")
            except discord.Forbidden:
                log.warning("Cannot remove role %s from %s", old_role.id, user.id)

    new_role = interaction.guild.get_role(target_role_id)
    if new_role and new_role not in user.roles:
        try:
            await user.add_roles(new_role, reason=f"Promotion by {interaction.user}")
        except discord.Forbidden:
            log.warning("Cannot add role %s to %s", target_role_id, user.id)

    await sync_member_tags(user)
    database.set_promo_lock(user.id, False)

    embed = discord.Embed(
        title="🎉 Manual Promotion!",
        description=f"{user.mention} has been promoted!",
        color=discord.Color.gold()
    )
    embed.add_field(name="Previous Rank", value=current_rank_name if current_rank_name != "None" else "No rank", inline=True)
    embed.add_field(name="New Rank", value=target_rank_name, inline=True)
    embed.add_field(name="Promoted By", value=interaction.user.mention, inline=False)
    apply_galaxy_theme(embed)

    autopromo_channel_id = database.get_config("autopromo_channel_id")
    if autopromo_channel_id:
        try:
            channel = interaction.guild.get_channel(int(autopromo_channel_id))
            me = interaction.guild.me or interaction.guild.get_member(bot.user.id)
            if channel and me and channel.permissions_for(me).send_messages:
                await channel.send(embed=embed)
        except Exception:
            log.exception("Error sending promotion message")

    log.info("PROMOTE %s by %s: %s -> %s",
             user.id, interaction.user.id, current_rank_name, target_rank_name)
    await interaction.followup.send(embed=embed)


@rank_group.command(name="demote", description="Demote a user to a lower rank (or remove rank entirely)")
@app_commands.describe(
    user="User to demote",
    rank_name="Target rank name (leave empty to strip all ranks)",
    lock="Block auto-promotion until unlocked (recommended)",
    reason="Reason for the demotion"
)
@is_mod()
async def rank_demote(
    interaction: discord.Interaction,
    user: discord.Member,
    rank_name: str = None,
    lock: bool = True,
    reason: str = "No reason provided"
):
    await interaction.response.defer(ephemeral=False)

    current_rank_id = database.get_user_rank(user.id)
    current_rank_data = database.get_rank_by_id(current_rank_id) if current_rank_id else None
    current_rank_name = current_rank_data[1] if current_rank_data else "None"
    current_threshold = current_rank_data[2] if current_rank_data else -1

    target_rank = None
    if rank_name:
        target_rank = database.get_rank_by_name(rank_name)
        if not target_rank:
            await interaction.followup.send(
                f"❌ Rank **{rank_name}** not found. Use `/rank list`.",
                ephemeral=True
            )
            return

        t_id, t_name, t_threshold, t_role_id, _ = target_rank

        if current_rank_data and t_threshold >= current_threshold:
            await interaction.followup.send(
                f"❌ **{t_name}** is not lower than **{current_rank_name}**. Use `/rank promote` instead.",
                ephemeral=True
            )
            return

    for rank_data in database.get_ranks():
        old_role = interaction.guild.get_role(rank_data[3])
        if old_role and old_role in user.roles:
            try:
                await user.remove_roles(old_role, reason=f"Demotion: {reason}")
            except discord.Forbidden:
                log.warning("Cannot remove role %s from %s", old_role.id, user.id)

    if target_rank:
        t_id, t_name, t_threshold, t_role_id, _ = target_rank
        database.set_user_rank(user.id, t_id)
        new_role = interaction.guild.get_role(t_role_id)
        if new_role:
            try:
                await user.add_roles(new_role, reason=f"Demotion: {reason}")
            except discord.Forbidden:
                log.warning("Cannot add role %s to %s", t_role_id, user.id)
        new_rank_display = t_name
    else:
        database.clear_user_rank(user.id)
        new_rank_display = "No rank"

    if lock:
        database.set_promo_lock(user.id, True, reason)
    else:
        database.set_promo_lock(user.id, False)

    await sync_member_tags(user)

    embed = discord.Embed(
        title="⬇️ Demotion",
        description=f"{user.mention} has been demoted.",
        color=discord.Color.dark_red()
    )
    embed.add_field(name="Previous Rank", value=current_rank_name, inline=True)
    embed.add_field(name="New Rank", value=new_rank_display, inline=True)
    embed.add_field(name="NP", value=f"{database.get_np(user.id)} NP (unchanged)", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Demoted By", value=interaction.user.mention, inline=False)
    if lock:
        embed.add_field(name="Auto-Promo", value="🔒 Locked", inline=False)
    else:
        embed.add_field(name="Auto-Promo", value="🔓 Unlocked", inline=False)
    apply_galaxy_theme(embed)

    autopromo_channel_id = database.get_config("autopromo_channel_id")
    if autopromo_channel_id:
        try:
            channel = interaction.guild.get_channel(int(autopromo_channel_id))
            me = interaction.guild.me or interaction.guild.get_member(bot.user.id)
            if channel and me and channel.permissions_for(me).send_messages:
                await channel.send(embed=embed)
        except Exception:
            log.exception("Error sending demotion message")

    log.info("DEMOTE %s by %s: %s -> %s (lock=%s) reason=%s",
             user.id, interaction.user.id, current_rank_name, new_rank_display, lock, reason)
    await interaction.followup.send(embed=embed)


@rank_group.command(name="unlock", description="Re-allow auto-promotion for a user")
@app_commands.describe(user="User to unlock")
@is_mod()
async def rank_unlock(interaction: discord.Interaction, user: discord.Member):
    database.set_promo_lock(user.id, False)
    await interaction.response.send_message(f"🔓 {user.mention} can be auto-promoted again.", ephemeral=True)


async def rank_name_autocomplete(interaction: discord.Interaction, current: str):
    ranks = database.get_ranks()
    return [
        app_commands.Choice(name=f"{r[1]} ({r[2]} NP)", value=r[1])
        for r in ranks if current.lower() in r[1].lower()
    ][:25]


rank_promote.autocomplete("rank_name")(rank_name_autocomplete)
rank_demote.autocomplete("rank_name")(rank_name_autocomplete)


@rank_group.command(name="nphistory", description="Show a user's NP change history")
@app_commands.describe(user="User to inspect")
@is_mod()
async def rank_nphistory(interaction: discord.Interaction, user: discord.Member):
    rows = database.get_np_history(user.id, limit=15)
    if not rows:
        await interaction.response.send_message(
            f"No NP history for {user.mention} yet - logging began when the audit table was added.",
            ephemeral=True
        )
        return

    lines = []
    for old, new, actor, source, note, at in rows:
        who = f"<@{actor}>" if actor else "system"
        line = f"`{at}` **{old}->{new}** ({new - old:+d}) - `{source}` - {who}"
        if note:
            line += f" - _{note}_"
        lines.append(line)

    embed = discord.Embed(
        title=f"NP History - {user.display_name}",
        description="\n".join(lines)[:4000],
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Current balance: {database.get_np(user.id)} NP")
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================
# STARTUP ROLES COMMANDS
# ============================================================

@startup_group.command(name="add", description="Add a role to assign when members join")
@app_commands.describe(role="The role to assign on join")
@is_mod()
async def startup_role_add(interaction: discord.Interaction, role: discord.Role):
    database.add_startup_role(role.id)
    embed = discord.Embed(
        title="✅ Startup Role Added",
        description=f"{role.mention} will now be assigned to new members.",
        color=discord.Color.green()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@startup_group.command(name="remove", description="Remove a startup role")
@app_commands.describe(role="The role to remove from startup assignment")
@is_mod()
async def startup_role_remove(interaction: discord.Interaction, role: discord.Role):
    database.remove_startup_role(role.id)
    embed = discord.Embed(
        title="✅ Startup Role Removed",
        description=f"{role.mention} will no longer be assigned to new members.",
        color=discord.Color.orange()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@startup_group.command(name="list", description="Show all startup roles")
async def startup_role_list(interaction: discord.Interaction):
    role_ids = database.get_startup_roles()
    if not role_ids:
        await interaction.response.send_message("No startup roles configured.", ephemeral=True)
        return

    lines = [f"• {interaction.guild.get_role(rid).mention}" for rid in role_ids if interaction.guild.get_role(rid)]
    embed = discord.Embed(title="📋 Startup Roles", description="\n".join(lines), color=discord.Color.blurple())
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@startup_group.command(name="sync", description="Apply startup roles to all existing members")
@is_mod()
async def startup_role_sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    startup_roles = database.get_startup_roles()
    if not startup_roles:
        await interaction.followup.send("No startup roles configured.", ephemeral=True)
        return

    roles_to_add = [interaction.guild.get_role(role_id) for role_id in startup_roles]
    roles_to_add = [r for r in roles_to_add if r is not None]

    if not roles_to_add:
        await interaction.followup.send("No valid startup roles found.", ephemeral=True)
        return

    count = 0
    failed = 0
    for member in interaction.guild.members:
        if member.bot:
            continue

        roles_needed = [r for r in roles_to_add if r not in member.roles]
        if roles_needed:
            try:
                await member.add_roles(*roles_needed)
                count += 1
            except discord.Forbidden:
                failed += 1

    embed = discord.Embed(
        title="✅ Startup Roles Synced",
        description=f"Applied startup roles to {count} members",
        color=discord.Color.green()
    )
    if failed > 0:
        embed.add_field(name="Failed", value=f"{failed} members (permissions issue)", inline=False)
    apply_galaxy_theme(embed)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ============================================================
# AWARDS COMMANDS
# ============================================================

@award_group.command(name="give", description="Give a role-based award to a user")
@app_commands.describe(user="User to award", role="Award role to give", np_bonus="Optional NP bonus")
@is_mod()
async def award_give(interaction: discord.Interaction, user: discord.Member,
                     role: discord.Role, np_bonus: app_commands.Range[int, 0, 1000000] = 0):
    try:
        if role not in user.roles:
            await user.add_roles(role)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have permission to add that role.", ephemeral=True)
        return

    database.award_role_to_user(user.id, role.id, np_bonus=np_bonus, actor_id=interaction.user.id)

    embed = discord.Embed(
        title="🎉 Award Granted!",
        description=f"{user.mention} received {role.mention}",
        color=discord.Color.gold()
    )
    if np_bonus:
        embed.add_field(name="NP Bonus", value=f"+{np_bonus} NP", inline=False)
        embed.add_field(name="New Balance", value=f"{database.get_np(user.id)} NP", inline=False)
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed)


@award_group.command(name="remove", description="Remove a role-based award from a user")
@app_commands.describe(user="User to remove award from", role="Award role to remove")
@is_mod()
async def award_remove(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    try:
        if role in user.roles:
            await user.remove_roles(role)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have permission to remove that role.", ephemeral=True)
        return

    removed = database.remove_award(user.id, role.id)
    if not removed:
        await interaction.response.send_message("That award was not found in the database.", ephemeral=True)
        return

    embed = discord.Embed(
        title="✅ Award Removed",
        description=f"Removed {role.mention} from {user.mention} and deleted the database record.",
        color=discord.Color.orange()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed)


# ============================================================
# CONFIG COMMANDS
# ============================================================

@config_group.command(name="autopromo-channel", description="Set the channel used for auto-promotion messages")
@app_commands.describe(channel="Channel where rank-up messages should be sent")
@is_mod()
async def config_autopromo_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    database.set_config("autopromo_channel_id", str(channel.id))
    await interaction.response.send_message(f"✅ Auto-promotion channel set to {channel.mention}.", ephemeral=True)


# ============================================================
# TAG COMMANDS
# ============================================================

@tag_group.command(name="set", description="Assign a tag to a role")
@app_commands.describe(role="The role to tag", tag="The tag text, e.g. [Roblox]")
@is_mod()
async def tag_set(interaction: discord.Interaction, role: discord.Role, tag: str):
    database.set_role_tag(role.id, tag)
    await interaction.response.send_message(
        f"✅ Tag `{tag}` set for role {role.mention}. Run `/tag sync` to apply it to existing members.",
        ephemeral=True
    )


@tag_group.command(name="remove", description="Remove the tag from a role")
@app_commands.describe(role="The role to remove the tag from")
@is_mod()
async def tag_remove(interaction: discord.Interaction, role: discord.Role):
    database.remove_role_tag(role.id)
    await interaction.response.send_message(
        f"✅ Tag removed from role {role.mention}. Run `/tag sync` to apply it.",
        ephemeral=True
    )


@tag_group.command(name="list", description="List all role -> tag mappings")
async def tag_list(interaction: discord.Interaction):
    mappings = database.get_all_role_tags()
    if not mappings:
        await interaction.response.send_message("No tags configured.", ephemeral=True)
        return

    lines = []
    for role_id, tag in mappings:
        role = interaction.guild.get_role(role_id)
        role_name = role.mention if role else f"Unknown Role ({role_id})"
        lines.append(f"{role_name} -> `{tag}`")

    embed = discord.Embed(
        title="Role Tags",
        description="\n".join(lines),
        color=discord.Color.blurple()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tag_group.command(name="sync", description="Re-apply tags to all members")
@is_mod()
async def tag_sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    count = 0
    for member in interaction.guild.members:
        await sync_member_tags(member)
        count += 1
    await interaction.followup.send(f"✅ Synced tags for {count} members.", ephemeral=True)


# ============================================================
# ERROR HANDLERS
# ============================================================

async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        msg = "🚫 You don't have permission to use this command."
    else:
        cmd = interaction.command.name if interaction.command else "unknown"
        log.exception("Command '/%s' failed (user=%s guild=%s)",
                      cmd, interaction.user.id,
                      interaction.guild.id if interaction.guild else None,
                      exc_info=error)
        msg = (f"⚠️ `/{cmd}` failed: `{type(error).__name__}`.\n"
               "No changes were saved. An admin should check the logs.")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


bot.tree.on_error = on_app_command_error

# ============================================================
# ADD COMMAND GROUPS
# ============================================================

bot.tree.add_command(tag_group)
bot.tree.add_command(rank_group)
bot.tree.add_command(startup_group)
bot.tree.add_command(award_group)
bot.tree.add_command(config_group)

log.info("Starting bot...")
bot.run(TOKEN, log_handler=None)
