import discord
import os
from discord.ext import commands
from discord import app_commands


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
# FACEIT MESSAGE LOGIC
# =========================

async def send_faceit_message(guild, needed=None):
    voice_channel = guild.get_channel(VOICE_CHANNEL_ID)
    text_channel = guild.get_channel(TEXT_CHANNEL_ID)

    if not voice_channel or not text_channel:
        return False, "Channels not found."

    # Button mode calculates from VC count
    if needed is None:
        real_members = [
            member for member in voice_channel.members
            if not member.bot
        ]

        current_count = len(real_members)
        needed = TARGET_PLAYERS - current_count

    # Slash command mode trusts the number entered
    if needed <= 0:
        return False, "5 stack is already full."

    if needed > TARGET_PLAYERS:
        return False, f"Need must be between 1 and {TARGET_PLAYERS}."

    role = discord.utils.find(
        lambda r: r.name.lower() == ROLE_NAME.lower(),
        guild.roles
    )

    message = f"{role.mention if role else '@faceit'} need {needed} more player(s)"

    await text_channel.send(
        message,
        allowed_mentions=discord.AllowedMentions(roles=True)
    )

    return True, message


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
        await interaction.response.defer(ephemeral=True)

        sent, result = await send_faceit_message(
            interaction.guild,
            needed=None
        )

        if sent:
            await interaction.followup.send(
                "Sent Faceit invite message.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                result,
                ephemeral=True
            )


# =========================
# SLASH COMMAND
# =========================

@bot.tree.command(
    name="faceit",
    description="Send a Faceit invite message"
)
@app_commands.describe(
    need="How many players you need, from 1 to 5"
)
async def faceit_command(
    interaction: discord.Interaction,
    need: app_commands.Range[int, 1, 5]
):
    await interaction.response.defer(ephemeral=True)

    sent, result = await send_faceit_message(
        interaction.guild,
        needed=need
    )

    if sent:
        await interaction.followup.send(
            f"Sent: need {need} more.",
            ephemeral=True
        )
    else:
        await interaction.followup.send(
            result,
            ephemeral=True
        )


# =========================
# READY
# =========================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    bot.add_view(FaceitView())

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Slash command sync error: {e}")


# =========================
# CONNECT TO VC
# =========================

async def ensure_voice_connected(guild):
    voice_channel = guild.get_channel(VOICE_CHANNEL_ID)
    text_channel = guild.get_channel(TEXT_CHANNEL_ID)

    if not voice_channel:
        return

    existing_vc = guild.voice_client

    if existing_vc:
        if existing_vc.channel.id != VOICE_CHANNEL_ID:
            await existing_vc.move_to(voice_channel)

        voice_clients[guild.id] = existing_vc
        return

    try:
        vc = await voice_channel.connect()
        voice_clients[guild.id] = vc

        if guild.id not in embed_sent and text_channel:
            embed = discord.Embed(
                title="Faceit Queue",
                description=(
                    "Click below to send Faceit invites.\n\n"
                    "Or use `/faceit need:3`."
                ),
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

    if len(real_members) == 0:
        vc = guild.voice_client

        if vc and vc.is_connected():
            try:
                await vc.disconnect()

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
    if member.bot:
        return

    guild = member.guild

    if after.channel and after.channel.id == VOICE_CHANNEL_ID:
        await ensure_voice_connected(guild)

    if before.channel and before.channel.id == VOICE_CHANNEL_ID:
        await disconnect_if_empty(guild)


# =========================
# START
# =========================

if not TOKEN:
    raise RuntimeError("TOKEN environment variable is missing.")

bot.run(TOKEN)
