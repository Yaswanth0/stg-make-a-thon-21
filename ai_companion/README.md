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

See the comments at the top of `companion.service`.

## Notes

- Reminders rely on the Pi's clock. Offline, it can't sync, and it only keeps
  time through a power cut if an RTC battery is fitted. When creating a
  reminder while `timedatectl` reports an unsynced clock, the assistant says so.
- Reminders due while the program was off are announced as "missed" at startup.
- The old `conversation_log.txt` is replaced by the `conversations` table:
  `sqlite3 companion.db "select * from conversations order by id desc limit 20"`.
