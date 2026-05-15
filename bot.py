import discord
import os
import re
import time
import asyncio
import audioop
import speech_recognition as sr

from collections import defaultdict
from discord.ext import commands, voice_recv


TOKEN = os.getenv("TOKEN")


VOICE_CHANNEL_ID = 1434700693175931020
TEXT_CHANNEL_ID = 384391311941435396

TARGET_PLAYERS = 5
ROLE_NAME = "faceit"

VOICE_COMMAND_COOLDOWN = 10
VOICE_CHUNK_SECONDS = 3.0


recognizer = sr.Recognizer()

# Store voice clients
voice_clients = {}

# Prevent duplicate embeds
embed_sent = set()

# Prevent voice command spam
last_voice_command = {}


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


# =========================
# VOICE COMMAND PARSING
# =========================

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "to": 2,
    "too": 2,
    "three": 3,
    "tree": 3,
    "four": 4,
    "for": 4,
    "five": 5,
}


def parse_faceit_voice_command(text: str):
    """
    Only accepts this command style:

    Hey faceit, we need 3 more

    Valid:
    - hey faceit we need 1 more
    - hey faceit we need 2 more
    - hey faceit we need three more
    - hey faceit we need five more

    Invalid:
    - yo faceit need 3 more
    - faceit need 3 more
    - hey faceit need 3 more
    - hey faceit we need 6 more
    """

    text = text.lower().strip()

    # Normalize common punctuation/spaces from speech recognition
    text = text.replace(",", " ")
    text = text.replace(".", " ")
    text = text.replace("!", " ")
    text = text.replace("?", " ")
    text = re.sub(r"\s+", " ", text)

    match = re.fullmatch(
        r"hey\s+faceit\s+we\s+need\s+(\d+|one|two|to|too|three|tree|four|for|five)\s+more",
        text
    )

    if not match:
        return None

    raw_number = match.group(1)

    if raw_number.isdigit():
        needed = int(raw_number)
    else:
        needed = NUMBER_WORDS.get(raw_number)

    if needed is None:
        return None

    # Maximum is 5
    if needed < 1 or needed > 5:
        return None

    return needed


# =========================
# FACEIT MESSAGE LOGIC
# =========================

async def send_faceit_message(guild, needed=None):
    voice_channel = guild.get_channel(VOICE_CHANNEL_ID)
    text_channel = guild.get_channel(TEXT_CHANNEL_ID)

    if not voice_channel or not text_channel:
        return False, "Channels not found."

    # Button mode:
    # If needed is None, calculate from current VC members.
    #
    # Voice command mode:
    # If needed is provided, trust the spoken number.
    if needed is None:
        real_members = [
            member for member in voice_channel.members
            if not member.bot
        ]

        current_count = len(real_members)
        needed = TARGET_PLAYERS - current_count

    if needed <= 0:
        return False, "5 stack is already full."

    if needed > TARGET_PLAYERS:
        return False, f"Cannot need more than {TARGET_PLAYERS} players."

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
# SPEECH TO TEXT
# =========================

def transcribe_pcm_chunk(pcm_bytes: bytes):
    """
    Discord decoded PCM is usually:
    - 48kHz
    - signed 16-bit
    - stereo

    SpeechRecognition wants mono audio, so we convert:
    stereo 48kHz -> mono 16kHz
    """

    try:
        # Convert stereo to mono
        mono = audioop.tomono(
            pcm_bytes,
            2,
            0.5,
            0.5
        )

        # Convert 48kHz to 16kHz
        mono_16k, _ = audioop.ratecv(
            mono,
            2,
            1,
            48000,
            16000,
            None
        )

        audio = sr.AudioData(
            mono_16k,
            16000,
            2
        )

        text = recognizer.recognize_google(audio)
        return text

    except sr.UnknownValueError:
        return ""

    except sr.RequestError as e:
        print(f"Speech recognition request error: {e}")
        return ""

    except Exception as e:
        print(f"Speech recognition error: {e}")
        return ""


async def handle_transcribed_audio(guild_id, user_id, pcm_bytes):
    loop = asyncio.get_running_loop()

    text = await loop.run_in_executor(
        None,
        transcribe_pcm_chunk,
        pcm_bytes
    )

    if not text:
        return

    print(f"Voice heard: {text}")

    needed = parse_faceit_voice_command(text)

    if needed is None:
        return

    now = time.monotonic()
    key = (guild_id, user_id)

    if now - last_voice_command.get(key, 0) < VOICE_COMMAND_COOLDOWN:
        print("Ignored duplicate voice command due to cooldown.")
        return

    last_voice_command[key] = now

    guild = bot.get_guild(guild_id)

    if not guild:
        return

    sent, result = await send_faceit_message(
        guild,
        needed=needed
    )

    if sent:
        print(f"Voice command sent message: {result}")
    else:
        print(f"Voice command ignored: {result}")


# =========================
# AUDIO SINK
# =========================

class FaceitVoiceSink(voice_recv.AudioSink):
    def __init__(self, bot_instance, guild_id):
        super().__init__()

        self.bot = bot_instance
        self.guild_id = guild_id

        self.buffers = defaultdict(bytearray)
        self.last_process_time = defaultdict(float)

        self.sample_rate = 48000
        self.channels = 2
        self.sample_width = 2

        self.target_bytes = int(
            self.sample_rate *
            self.channels *
            self.sample_width *
            VOICE_CHUNK_SECONDS
        )

    def wants_opus(self):
        # False means we want decoded PCM audio.
        return False

    def write(self, user, data: voice_recv.VoiceData):
        if user is None:
            return

        if getattr(user, "bot", False):
            return

        if not data.pcm:
            return

        user_buffer = self.buffers[user.id]
        user_buffer.extend(data.pcm)

        if len(user_buffer) < self.target_bytes:
            return

        now = time.monotonic()

        # Avoid sending chunks from the same user too aggressively
        if now - self.last_process_time[user.id] < 1.5:
            return

        self.last_process_time[user.id] = now

        pcm_chunk = bytes(user_buffer[:self.target_bytes])

        # Keep some overlap so the command is less likely to be cut off
        del user_buffer[:self.target_bytes // 2]

        asyncio.run_coroutine_threadsafe(
            handle_transcribed_audio(
                self.guild_id,
                user.id,
                pcm_chunk
            ),
            self.bot.loop
        )

    def cleanup(self):
        self.buffers.clear()


def start_voice_listener(guild, vc):
    if not hasattr(vc, "listen"):
        print("Voice client does not support receiving audio.")
        return

    if vc.is_listening():
        return

    sink = FaceitVoiceSink(
        bot,
        guild.id
    )

    vc.listen(
        sink,
        after=lambda e: print(f"Voice listener stopped: {e}") if e else None
    )

    print("Voice listener started.")


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

        # Make sure listener is running
        start_voice_listener(
            guild,
            existing_vc
        )

        return

    try:
        vc = await voice_channel.connect(
            cls=voice_recv.VoiceRecvClient
        )

        voice_clients[guild.id] = vc

        start_voice_listener(
            guild,
            vc
        )

        # Send embed once per session
        if guild.id not in embed_sent and text_channel:
            embed = discord.Embed(
                title="Faceit Queue",
                description=(
                    "Click below to send Faceit invites.\n\n"
                    "Voice command:\n"
                    "`Hey faceit, we need 3 more`"
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

    # Disconnect only if empty
    if len(real_members) == 0:
        vc = guild.voice_client

        if vc and vc.is_connected():
            try:
                if hasattr(vc, "is_listening") and vc.is_listening():
                    vc.stop_listening()

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
