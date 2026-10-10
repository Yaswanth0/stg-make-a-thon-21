# Voice companion

Offline voice assistant for the Raspberry Pi 5. One Python process and one
Ollama model; each "agent" is a class with its own prompt.

```
mic → Whisper → state machine (sleep / running / aborted)
                      ↓
                  Conductor ──┬→ Scheduler   reminders (+ background announcer)
                              ├→ Researcher  DuckDuckGo search, summarised
                              ├→ Archivist   saves facts you tell it
                              └→ Responder   answers, using history + facts + reminders
                                   ↓
                             companion.db (conversations, memories, reminders)
```

## Setup (on the Pi)

```bash
sudo apt install espeak-ng portaudio19-dev
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
ollama pull llama3.2:3b
ollama pull nomic-embed-text    # meaning search over facts and history (optional)
```

The voice is Piper with the female voice `en_US-hfc_female-medium`
(`TTS_ENGINE` and `PIPER_VOICE` in `config.py`). Its model, about 60 MB,
downloads into `voices/` on the first start, so be online that once. If Piper
can't load, Rabbit falls back to espeak-ng. Kokoro (`TTS_ENGINE = "kokoro"`)
sounds more human but is too slow on a Pi 5; `python tts_benchmark.py` times
every option.

## Running

```bash
python main.py              # voice
python main.py --text       # type instead of speaking (starts awake)
python main.py --text --speak
python main.py --debug      # also logs LLM timings
COMPANION_MODEL=llama3.2:1b python main.py   # try another model
```

Things to say: "Rabbit" (wake), "remember my locker code is 4521",
"what's my locker code?", "remind me to call mom tomorrow at 6 pm",
"what are my reminders?", "cancel the reminder to call mom",
"search for the price of a Raspberry Pi 5", "sleep", "mayday" (exit).

## Responsible AI

See [RESPONSIBLE_AI.md](RESPONSIBLE_AI.md). By voice: "are you a human?",
"what data do you store?", "what do you know about me?", "forget my locker
code", "forget everything", "private mode" / "private mode off", "repeat
that", "speak slower" / "speak faster". `python rai_report.py` shows what is
stored and what the guardrails did.

## Guardrails

`guardrails.py` checks every request before any agent runs, and every reply
before it is spoken:

| Before (what you said) | Rabbit |
|---|---|
| Emergency ("chest pain", "kitchen is on fire") | "Call 112 right now" |
| Self-harm | A caring reply with Tele-MANAS (14416) and 112 |
| Dangerous request (bombs, weapons, poison, hacking) | Declines |
| "Ignore your instructions", "reveal your prompt" | Declines |
| Card number, OTP, CVV, bank PIN, Aadhaar | Not stored; digits masked in history |

After (the reply): leaked instructions or dangerous content are replaced,
emoji and markdown removed, long replies cut at a sentence
(`MAX_REPLY_CHARS`), empty replies get a fallback. The log shows each one as
`guardrails: Guardrail before reply (...)`. `GUARDRAILS_ENABLED` in
`config.py` turns them off.

## How Rabbit finds things it was told

Saved facts and past conversations are searched two ways: by keyword (SQLite
full-text search, with synonyms) and by meaning (embeddings from
`nomic-embed-text`, so "when do I see my physician?" finds "doctor appointment
with Dr Rao"). Keyword matches come first. Everything is embedded in the
background, including history saved before. The log shows each meaning
search's scores (`Meaning search (memory): #3 0.71, ...`); raise or lower
`EMBED_MIN_SIMILARITY` in `config.py` if it finds too much or too little.
Without the model (or with `--no-embeddings`) search is keyword-only.

## Rocker switch

| Switch | What happens |
|---|---|
| ON | "Switched on." → RUNNING, no wake word needed |
| OFF | "Switched off." → OFF: mic and speaker off, nothing is heard or answered |
| OFF at startup | Starts in OFF and stays silent until switched ON |

Switching OFF cuts in immediately, even mid-answer. Reminders that come due
while OFF are announced when it's switched back ON. The program keeps running
the whole time; only "mayday" (while ON) ends it.

Wiring: one terminal to **GPIO17 (physical pin 11)**, the other to **GND
(physical pin 9)**. No resistor needed; the Pi's internal pull-up is used.
Change the pin with `SWITCH_PIN` in `config.py`.

Run without the switch: `python main.py --no-switch`. Without GPIO
(a laptop), it's skipped automatically with a warning.

## Music

"Play a song" (or "play some music", "play something") plays a random song
from `music/`. While it plays: "pause", "resume", "next song", "stop the
music", "what song is this?". The music goes quiet while Rabbit speaks and for
a few seconds after you say "Rabbit"; lyrics are ignored unless you say
"Rabbit" first. Switching OFF stops the music. Needs `sudo apt install mpv`.
Songs aren't in git: copy them into `music/` on the Pi (see `music/README.md`).

## To-do lists

| Say | Rabbit |
|---|---|
| "Create a todo list" | asks for a name, then for items until you say "done" |
| "Create a shopping list" | same, already named "shopping" |
| "Add milk to my groceries list" | adds it (makes the list if needed) |
| "Mark milk as done", "check off eggs", "milk is done" | ticks it off |
| "Remove bread from the groceries list" | deletes the item |
| "Delete the groceries list" | asks yes / no first |
| "What's on my groceries list?", "What are my lists?" | reads them |

While adding, each sentence is an item ("milk and eggs" is two). Lists are
kept in `companion.db`. No LLM is used.

## Tic-tac-toe

"Let's play tic-tac-toe" (or "play a game") starts a game; who goes first is
random. You're X, Rabbit is O, cells are 1-9 left to right, top to bottom:

    1 | 2 | 3
    4 | 5 | 6
    7 | 8 | 9

Say a cell ("5", "five", "cell 7", "top left", "centre"), "where are we?" for
the board, "restart" for a new game, or "quit". After each game Rabbit asks
whether to play again. The OLED shows the board. Difficulty: `GAME_LEVEL` in
`config.py` ("easy", "medium", "hard" = never loses).

## Status LEDs

| LED | GPIO (physical pin) | Resistor | Lit when |
|---|---|---|---|
| Red | GPIO22 (15) | 220 Ω | Rabbit is on (SLEEP or RUNNING) |
| White | GPIO23 (16) | 68 Ω | Thinking: working out a reply |
| Yellow | GPIO24 (18) | 220 Ω | The Researcher is using the internet |

Each LED: GPIO pin → resistor → long leg (+); short leg (−) → GND (physical
pin 14). Pins are in `config.py` (`LED_*_PIN`, `None` = not fitted);
`--no-leds` turns them off.

## OLED screen

A 128x64 I2C OLED shows an animated rabbit face while Rabbit is on:

| Rabbit is... | Face |
|---|---|
| awake | eyes open, blinks now and then |
| asleep | eyes closed, z's drifting up |
| hearing you | wide eyes, sound waves by the ears |
| thinking | eyes up, thought bubble filling with dots |
| searching online | eyes scanning, magnifying glass sweeping |
| speaking | mouth opening and closing |
| saving a fact | ^ ^ eyes and a heart |
| announcing a reminder | wide eyes, flashing "!" |
| unable to make out speech | raised eyebrow and "?" |
| switched off | blank |

| OLED pin | Pi pin |
|---|---|
| GND | GND (physical pin 6) |
| VCC | 3.3 V (physical pin 1) |
| SCL | GPIO3 / SCL (physical pin 5) |
| SDA | GPIO2 / SDA (physical pin 3) |

Setup once: `sudo raspi-config` -> Interface Options -> I2C -> Yes, reboot,
then `pip install luma.oled`. `i2cdetect -y 1` should show `3c` (or `3d`; set
`OLED_ADDRESS`). A 1.3" screen is usually an SH1106: `OLED_DRIVER = "sh1106"`.
Preview every face without a screen: `python display.py` (saves `rabbit_faces.png`). `--no-oled` skips it.

## Tests

```bash
python -m pytest                          # no Ollama, mic or internet needed
python -m tests.eval_conductor            # routing accuracy with the real model
python -m tests.eval_conductor --llm-only # the model alone, without keyword shortcuts
```

## Auto-start

Make Rabbit start by itself when the Pi is switched on (run once, as your
normal user, from this folder):

    bash install_autostart.sh

It installs a systemd user service with the right paths, starts it at boot
without anyone logging in, makes Ollama start at boot too, and warns about
missing permissions. Rabbit is up about 10-20 seconds after boot.

    journalctl --user-unit companion -f     # watch the log
    systemctl --user restart companion    # after a git pull or config change
    systemctl --user stop companion       # before running python main.py by hand
    bash install_autostart.sh --remove    # turn auto-start off

A crash restarts Rabbit; "mayday" stops it until the next boot.

## Notes

- Reminders rely on the Pi's clock. Offline, it can't sync, and it only keeps
  time through a power cut if an RTC battery is fitted. When creating a
  reminder while `timedatectl` reports an unsynced clock, the assistant says so.
- Reminders due while the program was off are announced as "missed" at startup.
- The old `conversation_log.txt` is replaced by the `conversations` table:
  `sqlite3 companion.db "select * from conversations order by id desc limit 20"`.
