import os
import discord
import re
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import database

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

database.init_db()
database.init_role_tags_table()

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} — slash commands synced.")

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles != after.roles:
        await sync_member_tags(after)

def is_mod():
    """Restrict command to users with Manage Server permission (adjust as needed)."""
    def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.manage_guild
    return app_commands.check(predicate)


TAG_PATTERN = re.compile(r"^(\[.*?\])+\s*")

def strip_tags(name: str) -> str:
    """Remove any existing [tag] prefixes from a name to get the clean base name."""
    return TAG_PATTERN.sub("", name).strip()

async def sync_member_tags(member: discord.Member):
    """Rebuild a member's nickname based on tags attached to their current roles."""
    if member.bot:
        return

    role_tags = dict(database.get_all_role_tags())  # {role_id: tag}
    # Sort member's roles by position (highest role first) so tag order is consistent
    member_roles_sorted = sorted(member.roles, key=lambda r: r.position, reverse=True)

    tags = [role_tags[r.id] for r in member_roles_sorted if r.id in role_tags]

    base_name = strip_tags(member.nick or member.display_name)
    if tags:
        new_nick = f"{''.join(tags)} {base_name}"
    else:
        new_nick = base_name

    # Discord nickname limit is 32 characters
    new_nick = new_nick[:32]

    if member.nick != new_nick and new_nick != member.name:
        try:
            await member.edit(nick=new_nick)
        except discord.Forbidden:
            pass  # bot's role is below the member's top role, or missing permission

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
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="stats", description="Check your or another user's Nexus Points")
@app_commands.describe(user="(Optional) check another user's stats")
async def stats(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    balance = database.get_np(target.id)
    embed = discord.Embed(
        title=f"{target.display_name}'s Nexus Points",
        description=f"**{balance} NP**",
        color=discord.Color.blurple()
    )
    embed.set_thumbnail(url=target.display_avatar.url)
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
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="promote", description="Promote a user by swapping roles")
@app_commands.describe(user="The user to promote", newrole="Role to add", oldrole="Role to remove")
@is_mod()
async def promote(interaction: discord.Interaction, user: discord.Member, newrole: discord.Role, oldrole: discord.Role):
    if oldrole in user.roles:
        await user.remove_roles(oldrole)
    if newrole not in user.roles:
        await user.add_roles(newrole)

    await sync_member_tags(user)  # auto-update nickname tags if either role has one

    embed = discord.Embed(
        title="🎉 Promotion!",
        description=f"{user.mention} has been promoted from **{oldrole.name}** to **{newrole.name}**.",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed)
    
tag_group = app_commands.Group(name="tag", description="Manage automatic role tags")

@tag_group.command(name="set", description="Assign a tag to a role (members with this role get the tag in their nickname)")
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
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tag_group.command(name="sync", description="Re-apply tags to all members based on current roles")
@is_mod()
async def tag_sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    count = 0
    for member in interaction.guild.members:
        await sync_member_tags(member)
        count += 1
    await interaction.followup.send(f"✅ Synced tags for {count} members.", ephemeral=True)

bot.tree.add_command(tag_group)

@np.error
@removenp.error
async def perm_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    else:
        raise error

@promote.error
async def promote_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    else:
        raise error

bot.run(TOKEN)