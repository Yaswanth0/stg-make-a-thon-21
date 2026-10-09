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

# ---------------------------------------------------------------- audio
TTS_WAV = "/tmp/tts_output.wav"
TTS_ENGINE = "piper"         # "piper" = natural voice, "espeak" = robotic but tiny
# Piper voices: https://rhasspy.github.io/piper-samples/ (listen before choosing).
#   en_US-lessac-medium  clear, neutral US voice (default)
#   en_US-amy-medium     US female        en_US-ryan-medium     US male
#   en_GB-alba-medium    UK female        en_GB-northern_english_male-medium
#   "-high" versions sound a little better but take longer to speak on the Pi.
PIPER_VOICE = "en_US-lessac-medium"
PIPER_VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
PIPER_LENGTH_SCALE = 1.0     # speaking speed: 0.9 = a bit faster, 1.1 = a bit slower
SPEECH_RATE = 150            # espeak-ng only: words per minute

MIC_NAME_HINT = "USB"        # the mic is picked by name, so a changed index is fine
MIC_FALLBACK_INDEX = 1
MIC_SAMPLE_RATE = 48000

WHISPER_MODEL = "base.en"    # "tiny.en" is faster, less accurate

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
HISTORY_TURNS = 4            # past turns the Responder sees
HISTORY_MAX_AGE = 30 * 60    # seconds; older turns are left out of the context
MEMORY_MATCHES = 3           # memories the Responder sees per question

# ---------------------------------------------------------------- reminders
REMINDER_CHECK_INTERVAL = 5  # seconds between checks for due reminders
MISSED_REMINDER_GRACE = 5 * 60  # due longer ago than this = announced as "missed"
FOLLOWUP_TIMEOUT = 60        # seconds to wait for "when?" after "remind me to ..."

# ---------------------------------------------------------------- research
SEARCH_RESULTS = 3
SEARCH_TIMEOUT = 10          # seconds
PAGES_TO_READ = 2            # top results whose page text is read, not just the snippet
PAGE_TEXT_CHARS = 1200       # text kept per page; more = slower LLM answers
