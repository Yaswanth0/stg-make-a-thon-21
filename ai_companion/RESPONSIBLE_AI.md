# Responsible AI in Rabbit

Rabbit is a voice assistant for a home. It hears what people say in that home
and remembers what they tell it, so it has to be honest, private, fair, safe
and under the user's control. This page states how it does that, and where
it falls short.

## What Rabbit is

- An **AI**, not a person. Asked "are you human?", it says so, and that it can
  make mistakes.
- A **small language model** (llama3.2:3b) running **on the Raspberry Pi**.
  It is much less capable than large cloud models and can be wrong,
  especially about facts and numbers.
- Speech recognition (Whisper) can mishear, especially with accents and
  noise. Rabbit reads back facts and reminder times so mistakes are caught.

## Principles and how they are built in

| Principle | In Rabbit | Where |
|---|---|---|
| **Privacy by design** | Speech, the model, memory and history all stay on the Pi. Only web searches go online, and only the question is sent. | whole design |
| **User control of data** | "What do you know about me?", "forget my locker code", "forget everything" (each deletion asks yes/no first). "Private mode" saves nothing until turned off. | `agents/rai.py` |
| **Data minimisation** | Conversation history older than `HISTORY_RETENTION_DAYS` (90) is deleted automatically. Card numbers, OTPs, CVVs, bank PINs and Aadhaar numbers are never stored, and are masked in history. | `rai.py`, `guardrails.py` |
| **Transparency** | Honest about being an AI and about what it stores ("what data do you store?" gives real counts). Answers from the internet start with "From a web search". | `agents/rai.py`, `agents/researcher.py` |
| **Reliability** | Numbers in web answers must appear in the sources; facts about the user come only from saved facts; it never pretends to have searched. | `grounding.py`, `agents/responder.py` |
| **Safety** | Emergencies get "call 112" at once; self-harm gets a caring reply with the Tele-MANAS helpline (14416); dangerous requests and attempts to change its rules are declined. | `guardrails.py` |
| **Fairness** | The model is told never to stereotype groups (gender, religion, caste, region, nationality, age, disability); replies making sweeping claims about a group are replaced. | `prompts.py`, `guardrails.py` |
| **Human control** | A physical switch turns the microphone and speaker off. Deleting a list or data asks first. | `switch.py`, agents |
| **Accountability** | Guardrail triggers, deletions, private mode and retention are logged (secrets masked) in the `rai_events` table. `python rai_report.py` summarises them. | `rai.py`, `rai_report.py` |
| **Inclusiveness** | "Repeat that", "speak slower", "speak faster" (remembered). Works by voice alone; the OLED and LEDs show state without sound. | `agents/rai.py`, `tts.py` |

## What is stored, and where

Everything is in one SQLite file on the Pi, `companion.db`, which is never
uploaded (it is excluded from git):

| Data | Kept until |
|---|---|
| Conversations (what you said, Rabbit's reply) | 90 days, "forget everything", or deleting the file |
| Saved facts | "forget …", "forget everything", or deleting the file |
| Reminders, to-do lists, game results | You cancel or delete them |
| Audit events (no personal content) | Deleting the file |

## Known limits

- **Pattern-based guardrails** catch common phrasings, not every phrasing. An
  emergency described in unusual words may not trigger the 112 reply.
- **The fairness check** catches explicit sweeping claims; subtler bias in
  wording is only addressed by the prompt.
- **A small model** can still state wrong facts that contain no numbers;
  only numbers and saved facts are checked.
- **Anyone near the device can talk to it.** There is no voice
  identification, so anyone can ask what it knows about you. Use the switch,
  private mode, or "forget" for sensitive things.
- **Helpline and emergency numbers are for India** (112, Tele-MANAS 14416);
  change them in `guardrails.py` elsewhere.

## Reporting a problem

If Rabbit says something harmful, unfair or wrong, note the time and check
`journalctl --user-unit companion` and `python rai_report.py`, then fix the
pattern or prompt and add the conversation as a test (see `tests/`).
