import os
import discord
from discord.ext import commands

# =========================
# CONFIG
# =========================

TOKEN = "MTUwMzc0OTUwMDI2NTY5MzI3NA.GDXIXA.ZYSxEYlEKhPuI4sJ7lMXCdfSGqmbqQYokFnk7Y"

VOICE_CHANNEL_ID = 1434700693175931020
TEXT_CHANNEL_ID = 384391311941435396

TARGET_PLAYERS = 5
ROLE_NAME = "faceit"

# =========================
# INTENTS
# =========================

intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True

# =========================
# BOT
# =========================

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# Prevent duplicate embeds
embed_sent = set()

# =========================
# BUTTON VIEW
# =========================

class FaceitView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Send Faceit Invites",
        style=discord.ButtonStyle.green,
        emoji="🎮",
        custom_id="faceit_send_invites"
    )
    async def send_invite(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        guild = interaction.guild

        voice_channel = guild.get_channel(VOICE_CHANNEL_ID)
        text_channel = guild.get_channel(TEXT_CHANNEL_ID)

        if not voice_channel or not text_channel:
            await interaction.response.defer()
            return

        # Ignore bots
        real_members = [
            member for member in voice_channel.members
            if not member.bot
        ]

        current_count = len(real_members)
        needed = TARGET_PLAYERS - current_count

        # Already full
        if needed <= 0:
            await interaction.response.defer()
            return

        role = discord.utils.get(
            guild.roles,
            name=ROLE_NAME
        )

        if role:
            await text_channel.send(
                f"{role.mention} need {needed} more player(s)"
            )
        else:
            await text_channel.send(
                f"Need {needed} more player(s)"
            )

        # Silent button response
        await interaction.response.defer()

# =========================
# READY
# =========================

@bot.event
async def on_ready():

    print(f"Logged in as {bot.user}")

    # Persistent buttons
    bot.add_view(FaceitView())

# =========================
# CONNECT TO VC
# =========================

async def ensure_voice_connected(guild):

    voice_channel = guild.get_channel(VOICE_CHANNEL_ID)
    text_channel = guild.get_channel(TEXT_CHANNEL_ID)

    if not voice_channel:
        return

    existing_vc = guild.voice_client

    # Already connected
    if existing_vc:

        # Move if somehow wrong VC
        if existing_vc.channel.id != VOICE_CHANNEL_ID:
            await existing_vc.move_to(voice_channel)

        return

    try:

        # Connect bot
        await voice_channel.connect()

        # Send embed once per session
        if guild.id not in embed_sent and text_channel:

            embed = discord.Embed(
                title="Faceit Queue",
                description="Click below to send Faceit invites.",
                color=0x57F287
            )

            await text_channel.send(
                embed=embed,
                view=FaceitView()
            )

            embed_sent.add(guild.id)

    except discord.ClientException:
        pass

    except Exception as e:
        print(f"Voice connect error: {e}")

# =========================
# DISCONNECT IF EMPTY
# =========================

async def disconnect_if_empty(guild):

    voice_channel = guild.get_channel(VOICE_CHANNEL_ID)

    if not voice_channel:
        return

    real_members = [
        member for member in voice_channel.members
        if not member.bot
    ]

    # Empty VC
    if len(real_members) == 0:

        vc = guild.voice_client

        if vc and vc.is_connected():

            try:

                await vc.disconnect()

                # Allow new embed next session
                embed_sent.discard(guild.id)

                print("Disconnected because VC is empty.")

            except Exception as e:
                print(f"Disconnect error: {e}")

# =========================
# VOICE EVENTS
# =========================

@bot.event
async def on_voice_state_update(member, before, after):

    # Ignore bots
    if member.bot:
        return

    guild = member.guild

    # Joined target VC
    if after.channel and after.channel.id == VOICE_CHANNEL_ID:
        await ensure_voice_connected(guild)

    # Left target VC
    if before.channel and before.channel.id == VOICE_CHANNEL_ID:
        await disconnect_if_empty(guild)

# =========================
# START
# =========================

bot.run(TOKEN)
