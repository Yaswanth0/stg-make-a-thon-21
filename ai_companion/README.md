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

The voice is Kokoro, female voice `af_heart` (`TTS_ENGINE` and `KOKORO_VOICE` in
`config.py`). Its model, about 120 MB, downloads into `voices/` on the first
start, so be online that once. If Kokoro can't load, Rabbit falls back to Piper
(`PIPER_VOICE`, also female), then to espeak-ng.

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
