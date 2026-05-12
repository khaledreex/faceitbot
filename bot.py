import discord
import os
from discord.ext import commands

TOKEN = os.getenv("TOKEN")


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

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# Store voice clients
voice_clients = {}

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
            await interaction.response.send_message(
                "Channels not found.",
                ephemeral=True
            )
            return

        # Ignore bots
        real_members = [
            member for member in voice_channel.members
            if not member.bot
        ]

        current_count = len(real_members)
        needed = TARGET_PLAYERS - current_count

        if needed <= 0:
            await interaction.response.send_message(
                "5 stack is already full.",
                ephemeral=True
            )
            return

        role = discord.utils.find(
            lambda r: r.name.lower() == ROLE_NAME.lower(),
            guild.roles
        )

        message = f"{role.mention if role else '@faceit'} need {needed} more player(s)"

        # await interaction.response.send_message("Done", ephemeral=True)

        # await text_channel.send(message,allowed_mentions=discord.AllowedMentions(roles=True))


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

        # Move if wrong VC
        if existing_vc.channel.id != VOICE_CHANNEL_ID:
            await existing_vc.move_to(voice_channel)

        voice_clients[guild.id] = existing_vc
        return

    try:

        vc = await voice_channel.connect()

        voice_clients[guild.id] = vc

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

    # Disconnect only if empty
    if len(real_members) == 0:

        vc = guild.voice_client

        if vc and vc.is_connected():

            try:

                await vc.disconnect()

                # Allow new embed next time
                embed_sent.discard(guild.id)

                print("Disconnected because VC is empty.")

            except Exception as e:
                print(f"Disconnect error: {e}")

            voice_clients.pop(guild.id, None)


# =========================
# VOICE EVENTS
# =========================

@bot.event
async def on_voice_state_update(member, before, after):

    # Ignore bots
    if member.bot:
        return

    guild = member.guild

    # User joined target VC
    if after.channel and after.channel.id == VOICE_CHANNEL_ID:
        await ensure_voice_connected(guild)

    # User left target VC
    if before.channel and before.channel.id == VOICE_CHANNEL_ID:
        await disconnect_if_empty(guild)


# =========================
# START
# =========================

bot.run(TOKEN)
