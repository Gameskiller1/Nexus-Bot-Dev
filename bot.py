import os
import discord
import re
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import database

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

database.init_db()
database.init_role_tags_table()
database.init_ranking_table()
database.init_user_ranks_table()
database.init_startup_roles_table()
database.init_award_history_table()

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

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} — slash commands synced.")
    if not auto_rank_members.is_running():
        auto_rank_members.start()

@bot.event
async def on_member_join(member: discord.Member):
    """Assign startup roles when member joins."""
    if member.bot:
        return
    
    database.ensure_user(member.id)
    
    # Assign startup roles
    startup_roles = database.get_startup_roles()
    if startup_roles:
        roles_to_add = [member.guild.get_role(role_id) for role_id in startup_roles]
        roles_to_add = [r for r in roles_to_add if r is not None]
        if roles_to_add:
            try:
                await member.add_roles(*roles_to_add)
            except discord.Forbidden:
                print(f"Could not assign roles to {member} — insufficient permissions")
    
    # Send welcome message
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
                pass

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles != after.roles:
        await sync_member_tags(after)

@tasks.loop(minutes=1)
async def auto_rank_members():
    """Check all members and auto-rank them if they qualify."""
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            
            np_amount = database.get_np(member.id)
            rank_info = database.get_appropriate_rank(np_amount)
            
            if not rank_info:
                continue
            
            rank_id, rank_name, role_id = rank_info
            current_rank = database.get_user_rank(member.id)
            
            if current_rank != rank_id:
                # Update user's rank
                database.set_user_rank(member.id, rank_id)
                
                # Remove old rank roles and add new one
                for rank_data in database.get_ranks():
                    old_role = guild.get_role(rank_data[3])
                    if old_role and old_role in member.roles:
                        try:
                            await member.remove_roles(old_role)
                        except discord.Forbidden:
                            pass
                
                # Add new rank role
                new_role = guild.get_role(role_id)
                if new_role and new_role not in member.roles:
                    try:
                        await member.add_roles(new_role)
                    except discord.Forbidden:
                        pass
                
                # Sync tags
                await sync_member_tags(member)
                
                # Send rank-up message
                try:
                    embed = discord.Embed(
                        title="🎉 Rank Up!",
                        description=f"{member.mention} advanced to **{rank_name}**!",
                        color=discord.Color.gold()
                    )
                    apply_galaxy_theme(embed)
                    
                    # Try to find a general or announcements channel
                    for channel in guild.text_channels:
                        if channel.permissions_for(guild.me).send_messages:
                            await channel.send(embed=embed)
                            break
                except Exception as e:
                    print(f"Error sending rank-up message: {e}")

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
            pass

# ============================================================
# NP COMMANDS
# ============================================================

@bot.tree.command(name="np", description="Give Nexus Points to a user")
@app_commands.describe(user="The user to give NP to", amount="Amount of NP to give")
@is_mod()
async def np(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, None]):
    database.add_np(user.id, amount)
    new_balance = database.get_np(user.id)
    embed = discord.Embed(
        title="Nexus Points Awarded",
        description=f"{user.mention} received **{amount} NP**.\nNew balance: **{new_balance} NP**",
        color=discord.Color.green()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="removenp", description="Remove Nexus Points from a user")
@app_commands.describe(user="The user to remove NP from", amount="Amount of NP to remove")
@is_mod()
async def removenp(interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1, None]):
    database.remove_np(user.id, amount)
    new_balance = database.get_np(user.id)
    embed = discord.Embed(
        title="Nexus Points Removed",
        description=f"Removed **{amount} NP** from {user.mention}.\nNew balance: **{new_balance} NP**",
        color=discord.Color.red()
    )
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
        for rid, rname, threshold, role_id in database.get_ranks():
            if rid == current_rank_id:
                rank_name = rname
                break

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
        lines.append(f"**#{i}** — {name}: **{np_amount} NP**")

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

rank_group = app_commands.Group(name="rank", description="Manage ranks and auto-ranking")

@rank_group.command(name="add", description="Create a new rank milestone")
@app_commands.describe(
    name="Rank name",
    np_threshold="NP required to reach this rank",
    role="Role to assign at this rank"
)
@is_mod()
async def rank_add(interaction: discord.Interaction, name: str, np_threshold: app_commands.Range[int, 0, 1000000], role: discord.Role):
    database.add_rank(name, np_threshold, role.id)
    embed = discord.Embed(
        title="✅ Rank Created",
        description=f"**{name}** requires **{np_threshold} NP** and grants {role.mention}",
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
    for rank_id, name, threshold, role_id in ranks:
        role = interaction.guild.get_role(role_id)
        role_mention = role.mention if role else f"Unknown Role ({role_id})"
        lines.append(f"**{name}** — {threshold} NP → {role_mention}")

    embed = discord.Embed(title="📊 Rank System", description="\n".join(lines), color=discord.Color.blurple())
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@rank_group.command(name="remove", description="Delete a rank")
@app_commands.describe(rank_id="The ID of the rank to remove")
@is_mod()
async def rank_remove(interaction: discord.Interaction, rank_id: app_commands.Range[int, 1, None]):
    ranks = database.get_ranks()
    rank_to_delete = None
    for rid, rname, threshold, role_id in ranks:
        if rid == rank_id:
            rank_to_delete = (rid, rname, threshold, role_id)
            break
    
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

@rank_group.command(name="remove", description="Delete a rank")
@app_commands.describe(rank_id="The ID of the rank to remove")
@is_mod()
async def rank_remove(interaction: discord.Interaction, rank_id: app_commands.Range[int, 1, None]):
    ranks = database.get_ranks()
    rank_to_delete = None
    for rid, rname, threshold, role_id in ranks:
        if rid == rank_id:
            rank_to_delete = (rid, rname, threshold, role_id)
            break
    
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

# ============================================================
# STARTUP ROLES COMMANDS
# ============================================================

startup_group = app_commands.Group(name="startup-role", description="Manage roles assigned on join")

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
        
        # Only add roles they don't already have
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
# AWARDS COMMANDS (SIMPLIFIED - Role-based only)
# ============================================================

award_group = app_commands.Group(name="award", description="Give role-based awards")

@award_group.command(name="give", description="Give a role-based award to a user")
@app_commands.describe(user="User to award", role="Award role to give", np_bonus="Optional NP bonus")
@is_mod()
async def award_give(interaction: discord.Interaction, user: discord.Member, role: discord.Role, np_bonus: app_commands.Range[int, 0, 1000000] = 0):
    try:
        if role not in user.roles:
            await user.add_roles(role)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have permission to add that role.", ephemeral=True)
        return

    database.award_role_to_user(user.id, role.id, np_bonus=np_bonus)

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

    embed = discord.Embed(
        title="✅ Award Removed",
        description=f"Removed {role.mention} from {user.mention}.",
        color=discord.Color.orange()
    )
    apply_galaxy_theme(embed)
    await interaction.response.send_message(embed=embed)

# ============================================================
# TAG COMMANDS
# ============================================================

tag_group = app_commands.Group(name="tag", description="Manage automatic role tags")

@tag_group.command(name="set", description="Assign a tag to a role")
@app_commands.describe(role="The role to tag", tag="The tag text, e.g. [Roblox]")
@is_mod()
async def tag_set(interaction: discord.Interaction, role: discord.Role, tag: str):
    database.set_role_tag(role.id, tag)
    await interaction.response.send_message(f"✅ Tag `{tag}` set for role {role.mention}. Run `/tag sync` to apply it to existing members.", ephemeral=True)

@tag_group.command(name="remove", description="Remove the tag from a role")
@app_commands.describe(role="The role to remove the tag from")
@is_mod()
async def tag_remove(interaction: discord.Interaction, role: discord.Role):
    database.remove_role_tag(role.id)
    await interaction.response.send_message(f"✅ Tag removed from role {role.mention}. Run `/tag sync` to apply it.", ephemeral=True)

@tag_group.command(name="list", description="List all role → tag mappings")
async def tag_list(interaction: discord.Interaction):
    mappings = database.get_all_role_tags()
    if not mappings:
        await interaction.response.send_message("No tags configured.", ephemeral=True)
        return
    lines = []
    for role_id, tag in mappings:
        role = interaction.guild.get_role(role_id)
        role_name = role.mention if role else f"Unknown Role ({role_id})"
        lines.append(f"{role_name} → `{tag}`")
    embed = discord.Embed(title="Role Tags", description="\n".join(lines), color=discord.Color.blurple())
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

@np.error
@removenp.error
@rank_add.error
@startup_role_add.error
@award_give.error
@tag_set.error
async def perm_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    else:
        raise error

# ============================================================
# ADD COMMAND GROUPS
# ============================================================

bot.tree.add_command(tag_group)
bot.tree.add_command(rank_group)
bot.tree.add_command(startup_group)
bot.tree.add_command(award_group)

bot.run(TOKEN)