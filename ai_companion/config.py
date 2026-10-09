"""All settings in one place."""

import os

# ---------------------------------------------------------------- keywords
ASSISTANT_NAME = "Rabbit"    # used in every agent's prompt (prompts.py)
WAKE_WORDS = {"rabbit", "rabbits"}
SLEEP_WORD = "sleep"
EXIT_WORD = "mayday"
SILENCE_TIMEOUT = 120        # seconds of silence in RUNNING before going to sleep
SLEEP_COMMAND_MAX_WORDS = 4  # "sleep", "go to sleep" count; longer sentences don't

# ---------------------------------------------------------------- rocker switch
# ON = RUNNING, OFF = mic and speaker off. None = no switch fitted.
SWITCH_PIN = 17              # BCM numbering: GPIO17 is physical pin 11
SWITCH_DEBOUNCE = 0.3        # seconds a change must last before it counts

# ---------------------------------------------------------------- status LEDs
# BCM GPIO numbers; None = that LED isn't fitted. Wiring in README.md.
LED_POWER_PIN = 22           # red: on while Rabbit is on      (physical pin 15)
LED_THINKING_PIN = 23        # white: thinking                 (physical pin 16)
LED_INTERNET_PIN = 24        # yellow: Researcher on internet  (physical pin 18)

# ---------------------------------------------------------------- OLED screen
# 128x64 I2C OLED showing a rabbit face while Rabbit is on. Enable I2C once:
# sudo raspi-config -> Interface Options -> I2C. Find the address: i2cdetect -y 1
OLED_DRIVER = "ssd1306"      # "ssd1306" (0.96 inch, most common), "sh1106" (1.3 inch); None = no screen
OLED_ADDRESS = 0x3C          # most modules; some use 0x3D

# ---------------------------------------------------------------- audio
TTS_WAV = "/tmp/tts_output.wav"
# Piper: natural female voice, fast on the Pi 5. Kokoro sounds more human but
# measured x2.5 slower than real time on the Pi (tts_benchmark.py), so it
# pauses mid-reply. "espeak" = robotic, last resort.
TTS_ENGINE = "piper"         # "piper", "kokoro" or "espeak"
TTS_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

# Kokoro female voices, best first (a = American, b = British accent):
#   af_heart, af_bella, af_nicole (soft), af_sarah, bf_emma, bf_isabella
# Samples: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
KOKORO_VOICE = "af_heart"
KOKORO_SPEED = 1.0           # 1.1 = a bit faster, 0.9 = a bit slower
KOKORO_MODEL = "int8"        # "int8" (92 MB) or "fp32" (310 MB); run tts_benchmark.py to pick the faster

# Speed: speech starts once the first piece is synthesized, and the rest is
# made while it plays. A long first sentence is split at its first comma.
TTS_THREADS = 4              # CPU cores for Kokoro (the Pi 5 has 4)
TTS_FIRST_CHUNK_WORDS = 8    # first sentences longer than this are split at a comma
# Fixed replies, synthesized once at startup so they play instantly.
TTS_CACHED_PHRASES = [
    "Yes?", "Going to sleep.", "No activity. Going to sleep.", "Shutting down.",
    "Switched on.", "Switched off.", "System ready.",
]

# Piper female voices (hear them at the link below; change and restart to switch):
#   en_US-hfc_female-medium, en_US-amy-medium, en_US-kristin-medium, en_GB-jenny_dioco-medium
# Samples: https://rhasspy.github.io/piper-samples/
PIPER_VOICE = "en_US-hfc_female-medium"
PIPER_LENGTH_SCALE = 1.0     # speaking speed: 0.9 = a bit faster, 1.1 = a bit slower

ESPEAK_VOICE = "en-us+f3"    # last resort; "+f3" = female variant
SPEECH_RATE = 150            # espeak-ng only: words per minute

MIC_NAME_HINT = "USB"        # the mic is picked by name, so a changed index is fine
MIC_FALLBACK_INDEX = 1
MIC_SAMPLE_RATE = 48000

WHISPER_MODEL = "base.en"    # "small.en" understands much better but is ~3x slower
WHISPER_BEAM_SIZE = 1        # 5 = a little more accurate, slower
WHISPER_MIN_LOGPROB = -1.0   # Whisper's confidence; below this the speech is ignored
# Words Whisper should expect, so it spells them right instead of guessing.
WHISPER_PROMPT = ("Rabbit. Mayday. Sleep. Search for the weather in Hyderabad. "
                  "Cricket score of India versus West Indies. Virat Kohli. "
                  "Recipe for chicken tikka masala, biryani. Remind me to call Mom. Remember my locker code.")

# ---------------------------------------------------------------- LLM
# Sentences no keyword shortcut matches go to the Responder (False), or to
# the LLM to choose an agent (True: one extra LLM call, but it also catches
# unusual phrasings like "my car is parked on level 3" -> remember).
ROUTE_WITH_LLM = False

# 1b is too weak for routing and JSON extraction; 3b needs about 2 GB of RAM.
# Override without editing: COMPANION_MODEL=llama3.2:1b python main.py
LLM_MODEL = os.environ.get("COMPANION_MODEL", "llama3.2:3b")
LLM_KEEP_ALIVE = -1          # -1 = keep the LLM in RAM forever; "5m" = Ollama's default
LLM_LOAD_ATTEMPTS = 12       # 12 tries x 5 seconds = waits up to a minute for Ollama
# The agents' prompts are in prompts.py.
# Randomness of free-text answers: 0 = always the most likely words, ~0.8 =
# Ollama's default. Low values make the model stick to what it was given.
RESPONDER_TEMPERATURE = 0.3
RESEARCHER_TEMPERATURE = 0.1

# ---------------------------------------------------------------- storage
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "companion.db")
HISTORY_TURNS = 2            # past turns the Responder sees (more = slower, and misheard junk lingers)
HISTORY_MAX_AGE = 30 * 60    # seconds; older turns are left out of the context
MEMORY_MATCHES = 3           # memories the Responder sees per question
MEMORY_FALLBACK = 15         # a question about the user that matches nothing sees this many recent facts
RECALL_MATCHES = 4           # past conversation turns looked up for "what did you tell me about ...?"

# ---------------------------------------------------------------- reminders
REMINDER_CHECK_INTERVAL = 5  # seconds between checks for due reminders
MISSED_REMINDER_GRACE = 5 * 60  # due longer ago than this = announced as "missed"
FOLLOWUP_TIMEOUT = 60        # seconds to wait for "when?" after "remind me to ..."

# ---------------------------------------------------------------- music
# Songs in this folder; "play a song" picks one at random. Needs: sudo apt install mpv
MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
MUSIC_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".flac", ".wav", ".opus"}
MUSIC_VOLUME = 70            # percent
MUSIC_DUCK_VOLUME = 15       # while Rabbit speaks, and after "Rabbit" while you speak
MUSIC_LISTEN_SECONDS = 8     # how long the music stays quiet after "Rabbit"

# ---------------------------------------------------------------- tic-tac-toe
GAME_LEVEL = "medium"        # "easy" (random), "medium" (beatable), "hard" (never loses)
GAME_IDLE_TIMEOUT = 180      # seconds without a move before an unfinished game is dropped

# ---------------------------------------------------------------- research
SEARCH_RESULTS = 3
SEARCH_TIMEOUT = 10          # seconds
PAGES_TO_READ = 2            # top results whose page text is read, not just the snippet
PAGE_TEXT_CHARS = 600        # text kept per page; more = slower LLM answers
SNIPPET_CHARS = 300          # text kept per search result
HOME_CITY = "Hyderabad"      # weather when no place is named
HOME_COUNTRY = "IN"          # preferred country when a place name exists in several
