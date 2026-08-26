import os
import discord
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


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} — slash commands synced.")


def is_mod():
    """Restrict command to users with Manage Server permission (adjust as needed)."""
    def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.manage_guild
    return app_commands.check(predicate)


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



@np.error
@removenp.error
async def perm_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
    else:
        raise error


bot.run(TOKEN)