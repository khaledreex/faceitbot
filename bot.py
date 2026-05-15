import discord
import os
import re
import time
import asyncio
import audioop
import ctypes.util
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

DEBUG_AUDIO_PACKETS = True


recognizer = sr.Recognizer()

voice_clients = {}
embed_sent = set()
last_voice_command = {}

connecting_guilds = set()
ready_ran = False


# =========================
# OPUS LOADER
# =========================

def load_opus_library():
    if discord.opus.is_loaded():
        print("[OPUS] Already loaded.")
        return True

    opus_path = ctypes.util.find_library("opus")

    print(f"[OPUS] ctypes found opus: {opus_path}")

    if opus_path:
        try:
            discord.opus.load_opus(opus_path)
        except Exception as e:
            print(f"[OPUS] load_opus error: {e}")

    if discord.opus.is_loaded():
        print("[OPUS] Loaded successfully.")
        return True

    print("[OPUS] Failed to load.")
    print("[OPUS] On Railway, add: RAILPACK_DEPLOY_APT_PACKAGES=libopus0")
    return False


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
    original_text = text

    text = text.lower().strip()
    text = text.replace(",", " ")
    text = text.replace(".", " ")
    text = text.replace("!", " ")
    text = text.replace("?", " ")
    text = re.sub(r"\s+", " ", text)

    print(f"[PARSER] Normalized text: {text}")

    match = re.fullmatch(
        r"(hey|hay)\s+(faceit|face it|facet|facit)\s+we\s+need\s+(\d+|one|two|to|too|three|tree|four|for|five)\s+more",
        text
    )

    if not match:
        print(f"[PARSER] Rejected text: {original_text}")
        return None

    raw_number = match.group(3)

    if raw_number.isdigit():
        needed = int(raw_number)
    else:
        needed = NUMBER_WORDS.get(raw_number)

    if needed is None:
        print("[PARSER] Rejected: could not parse number.")
        return None

    if needed < 1 or needed > 5:
        print(f"[PARSER] Rejected: number outside 1-5: {needed}")
        return None

    print(f"[PARSER] Accepted command. Needed = {needed}")

    return needed


# =========================
# FACEIT MESSAGE LOGIC
# =========================

async def send_faceit_message(guild, needed=None):
    voice_channel = guild.get_channel(VOICE_CHANNEL_ID)
    text_channel = guild.get_channel(TEXT_CHANNEL_ID)

    if not voice_channel:
        print("[SEND] Voice channel not found.")
        return False, "Voice channel not found."

    if not text_channel:
        print("[SEND] Text channel not found.")
        return False, "Text channel not found."

    if needed is None:
        real_members = [
            member for member in voice_channel.members
            if not member.bot
        ]

        current_count = len(real_members)
        needed = TARGET_PLAYERS - current_count

        print(f"[SEND] Button mode. Current count = {current_count}, needed = {needed}")

    else:
        print(f"[SEND] Voice mode. Spoken needed = {needed}")

    if needed <= 0:
        print("[SEND] Ignored: 5 stack already full.")
        return False, "5 stack is already full."

    if needed > TARGET_PLAYERS:
        print(f"[SEND] Ignored: needed > {TARGET_PLAYERS}")
        return False, f"Cannot need more than {TARGET_PLAYERS} players."

    role = discord.utils.find(
        lambda r: r.name.lower() == ROLE_NAME.lower(),
        guild.roles
    )

    if role:
        print(f"[SEND] Found role: {role.name} / {role.id}")
    else:
        print("[SEND] Role not found. Falling back to plain @faceit text.")

    message = f"{role.mention if role else '@faceit'} need {needed} more player(s)"

    await text_channel.send(
        message,
        allowed_mentions=discord.AllowedMentions(roles=True)
    )

    print(f"[SEND] Sent message: {message}")

    return True, message


# =========================
# SPEECH TO TEXT
# =========================

def transcribe_pcm_chunk(pcm_bytes: bytes):
    print(f"[STT] Transcribing chunk. PCM bytes = {len(pcm_bytes)}")

    try:
        mono = audioop.tomono(
            pcm_bytes,
            2,
            0.5,
            0.5
        )

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

        print(f"[STT] Result: {text}")

        return text

    except sr.UnknownValueError:
        print("[STT] Could not understand audio.")
        return ""

    except sr.RequestError as e:
        print(f"[STT] Speech recognition request error: {e}")
        return ""

    except Exception as e:
        print(f"[STT] Speech recognition error: {e}")
        return ""


async def handle_transcribed_audio(guild_id, user_id, pcm_bytes):
    print(f"[HANDLE] Received chunk for guild={guild_id}, user={user_id}")

    loop = asyncio.get_running_loop()

    text = await loop.run_in_executor(
        None,
        transcribe_pcm_chunk,
        pcm_bytes
    )

    if not text:
        print("[HANDLE] No transcription text returned.")
        return

    print(f"[HANDLE] Voice heard: {text}")

    needed = parse_faceit_voice_command(text)

    if needed is None:
        print("[HANDLE] No valid Faceit command found.")
        return

    now = time.monotonic()
    key = (guild_id, user_id)

    if now - last_voice_command.get(key, 0) < VOICE_COMMAND_COOLDOWN:
        print("[HANDLE] Ignored duplicate voice command due to cooldown.")
        return

    last_voice_command[key] = now

    guild = bot.get_guild(guild_id)

    if not guild:
        print("[HANDLE] Guild not found.")
        return

    sent, result = await send_faceit_message(
        guild,
        needed=needed
    )

    if sent:
        print(f"[HANDLE] Voice command sent message: {result}")
    else:
        print(f"[HANDLE] Voice command ignored: {result}")


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
        self.last_packet_log_time = defaultdict(float)

        self.sample_rate = 48000
        self.channels = 2
        self.sample_width = 2

        self.target_bytes = int(
            self.sample_rate *
            self.channels *
            self.sample_width *
            VOICE_CHUNK_SECONDS
        )

        print(
            f"[SINK] Created. target_bytes={self.target_bytes}, "
            f"chunk_seconds={VOICE_CHUNK_SECONDS}"
        )

    def wants_opus(self):
        return False

    def write(self, user, data: voice_recv.VoiceData):
        if user is None:
            print("[SINK] Got packet with no user.")
            return

        if getattr(user, "bot", False):
            return

        if not data:
            print(f"[SINK] No data from user={user}")
            return

        if not data.pcm:
            print(f"[SINK] No PCM data from user={user}")
            return

        now = time.monotonic()

        if DEBUG_AUDIO_PACKETS:
            if now - self.last_packet_log_time[user.id] > 1:
                print(
                    f"[SINK] AUDIO PACKET from {user} | "
                    f"pcm bytes={len(data.pcm)} | "
                    f"buffer before={len(self.buffers[user.id])}"
                )
                self.last_packet_log_time[user.id] = now

        user_buffer = self.buffers[user.id]
        user_buffer.extend(data.pcm)

        if len(user_buffer) < self.target_bytes:
            return

        if now - self.last_process_time[user.id] < 1.5:
            return

        self.last_process_time[user.id] = now

        pcm_chunk = bytes(user_buffer[:self.target_bytes])

        del user_buffer[:self.target_bytes // 2]

        print(
            f"[SINK] Sending chunk to STT for user={user}. "
            f"chunk bytes={len(pcm_chunk)}, remaining buffer={len(user_buffer)}"
        )

        future = asyncio.run_coroutine_threadsafe(
            handle_transcribed_audio(
                self.guild_id,
                user.id,
                pcm_chunk
            ),
            self.bot.loop
        )

        def _done_callback(f):
            try:
                f.result()
            except Exception as e:
                print(f"[SINK] Error in transcription task: {e}")

        future.add_done_callback(_done_callback)

    def cleanup(self):
        print("[SINK] Cleanup called.")
        self.buffers.clear()


# =========================
# VOICE LISTENER
# =========================

def start_voice_listener(guild, vc):
    print(f"[LISTENER] Trying to start listener in guild={guild.name}")

    if not discord.opus.is_loaded():
        print("[LISTENER] Opus is not loaded. Trying to load now...")
        load_opus_library()

    if not discord.opus.is_loaded():
        print("[LISTENER] Cannot start listener because Opus is still not loaded.")
        return

    if not hasattr(vc, "listen"):
        print("[LISTENER] Voice client does not support receiving audio.")
        return

    if hasattr(vc, "is_listening") and vc.is_listening():
        print("[LISTENER] Voice listener already running.")
        return

    sink = FaceitVoiceSink(bot, guild.id)

    def after_listener(error):
        print(f"[LISTENER] Voice listener stopped: {error}")

        if error:
            print("[LISTENER] Restarting listener after error...")

            async def restart():
                await asyncio.sleep(2)

                current_vc = guild.voice_client

                if current_vc and current_vc.is_connected():
                    start_voice_listener(guild, current_vc)
                else:
                    await ensure_voice_connected(guild)

            asyncio.run_coroutine_threadsafe(
                restart(),
                bot.loop
            )

    vc.listen(
        sink,
        after=after_listener
    )

    print("[LISTENER] Voice listener started.")


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
# CONNECT TO VC
# =========================

async def ensure_voice_connected(guild):
    if guild.id in connecting_guilds:
        print("[VOICE] Already connecting. Skipping duplicate connect.")
        return

    connecting_guilds.add(guild.id)

    try:
        print(f"[VOICE] ensure_voice_connected called for guild={guild.name}")

        voice_channel = guild.get_channel(VOICE_CHANNEL_ID)
        text_channel = guild.get_channel(TEXT_CHANNEL_ID)

        if not voice_channel:
            print(f"[VOICE] Voice channel not found: {VOICE_CHANNEL_ID}")
            return

        print(f"[VOICE] Target voice channel found: {voice_channel.name}")

        if not discord.opus.is_loaded():
            print("[VOICE] Opus is not loaded before voice connect. Trying to load now...")
            load_opus_library()

        existing_vc = guild.voice_client

        if existing_vc and existing_vc.is_connected():
            print("[VOICE] Bot already connected.")

            if existing_vc.channel.id != VOICE_CHANNEL_ID:
                print("[VOICE] Moving bot to target voice channel...")
                await existing_vc.move_to(voice_channel)

            voice_clients[guild.id] = existing_vc

            start_voice_listener(guild, existing_vc)
            return

        if existing_vc:
            print("[VOICE] Cleaning broken voice client...")

            try:
                await existing_vc.disconnect(force=True)
            except Exception as e:
                print(f"[VOICE] Cleanup disconnect error: {e}")

            voice_clients.pop(guild.id, None)

        print("[VOICE] Connecting with VoiceRecvClient...")

        vc = await voice_channel.connect(
            cls=voice_recv.VoiceRecvClient,
            timeout=30,
            reconnect=True
        )

        voice_clients[guild.id] = vc

        print("[VOICE] Connected to voice.")

        start_voice_listener(guild, vc)

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

            print("[VOICE] Sent Faceit Queue embed.")

    except Exception as e:
        print(f"[VOICE] Voice connect error: {repr(e)}")

    finally:
        connecting_guilds.discard(guild.id)


# =========================
# DISCONNECT IF EMPTY
# =========================

async def disconnect_if_empty(guild):
    print(f"[VOICE] disconnect_if_empty called for guild={guild.name}")

    voice_channel = guild.get_channel(VOICE_CHANNEL_ID)

    if not voice_channel:
        print("[VOICE] Voice channel not found during disconnect check.")
        return

    real_members = [
        member for member in voice_channel.members
        if not member.bot
    ]

    print(f"[VOICE] Real members in VC: {len(real_members)}")

    if len(real_members) == 0:
        vc = guild.voice_client

        if vc and vc.is_connected():
            try:
                if hasattr(vc, "is_listening") and vc.is_listening():
                    print("[VOICE] Stopping voice listener...")
                    vc.stop_listening()

                await vc.disconnect()

                embed_sent.discard(guild.id)

                print("[VOICE] Disconnected because VC is empty.")

            except Exception as e:
                print(f"[VOICE] Disconnect error: {e}")

            voice_clients.pop(guild.id, None)


# =========================
# READY
# =========================

@bot.event
async def on_ready():
    global ready_ran

    if ready_ran:
        print("[READY] on_ready already ran. Skipping duplicate startup connect.")
        return

    ready_ran = True

    print(f"[READY] Logged in as {bot.user}")
    print(f"[READY] Guild count: {len(bot.guilds)}")

    load_opus_library()

    bot.add_view(FaceitView())

    await asyncio.sleep(3)

    for guild in bot.guilds:
        voice_channel = guild.get_channel(VOICE_CHANNEL_ID)

        if not voice_channel:
            print(f"[READY] Target VC not found in guild={guild.name}")
            continue

        real_members = [
            member for member in voice_channel.members
            if not member.bot
        ]

        print(
            f"[READY] Guild={guild.name}, "
            f"target VC={voice_channel.name}, "
            f"real members={len(real_members)}"
        )

        if len(real_members) > 0:
            print("[READY] Real users already in VC. Connecting listener...")
            await ensure_voice_connected(guild)


# =========================
# VOICE EVENTS
# =========================

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    guild = member.guild

    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None

    print(
        f"[VOICE_STATE] member={member} | "
        f"before={before_id} | after={after_id}"
    )

    if after.channel and after.channel.id == VOICE_CHANNEL_ID:
        print("[VOICE_STATE] User joined target VC.")
        await ensure_voice_connected(guild)

    if before.channel and before.channel.id == VOICE_CHANNEL_ID:
        print("[VOICE_STATE] User left target VC.")
        await disconnect_if_empty(guild)


# =========================
# START
# =========================

if not TOKEN:
    raise RuntimeError("TOKEN environment variable is missing.")

load_opus_library()

bot.run(TOKEN)
