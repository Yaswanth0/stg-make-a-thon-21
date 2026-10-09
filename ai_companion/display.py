"""OLED screen (128x64, I2C): an animated rabbit face that shows what Rabbit
is doing.

    awake       eyes open, blinks now and then
    asleep      eyes closed, z's drifting up
    hearing     wide eyes, sound waves by the ears (transcribing your speech)
    thinking    eyes up, thought bubble filling with dots
    searching   eyes sideways, magnifying glass sweeping (Researcher online)
    speaking    mouth opening and closing
    happy       ^ ^ eyes and a heart (a fact was saved)
    alert       wide eyes and a flashing "!" (a reminder is going off)
    confused    one eyebrow up and a "?" (speech it couldn't make out)
    off         blank screen

The program says what it is doing with `with screen.mood("thinking"):` or
`screen.flash("happy")`; a background thread draws the matching face about
six times a second. Uses luma.oled and I2C (sudo raspi-config -> Interface
Options -> I2C). Without the library or the screen, every call does nothing.
"""

import logging
import math
import threading
import time
from contextlib import contextmanager

import config

log = logging.getLogger("display")

WIDTH, HEIGHT = 128, 64
CX = WIDTH // 2
EYE_Y = 37
WHITE, BLACK = 255, 0
FRAME_SECONDS = 0.16

# Most important first: the face shows the first active one. "speaking" only
# moves the mouth, so it combines with whichever face is showing.
PRIORITY = ["alert", "happy", "confused", "searching", "thinking", "hearing"]
# While asleep the mic still listens for "Rabbit"; the face shouldn't react to
# every noise, only to a reminder going off.
ASLEEP_MOODS = {"alert", "speaking"}
FLASH_SECONDS = {"happy": 2.5, "confused": 2.0}


# ---------------------------------------------------------------- drawing
def draw_rabbit(draw, mood="awake", speaking=False, t=0.0):
    """Draws the rabbit in `mood` at time `t` (seconds, for animations)."""
    frame = int(t / FRAME_SECONDS)

    # Ears first; the head is drawn over their bottoms.
    perked = mood in ("hearing", "alert")
    for x in (CX - 17, CX + 5):
        top = 0 if perked else 2
        draw.ellipse((x, top, x + 12, 30), outline=WHITE, fill=BLACK)
        draw.ellipse((x + 4, top + 5, x + 8, 24), fill=WHITE)
    draw.ellipse((CX - 30, 19, CX + 30, 63), outline=WHITE, fill=BLACK)

    _eyes(draw, mood, t)
    _nose_and_mouth(draw, mood, speaking, frame)
    _whiskers(draw)
    _extras(draw, mood, frame)


def _eyes(draw, mood, t):
    for ex in (CX - 12, CX + 12):
        if mood == "asleep":
            draw.arc((ex - 5, EYE_Y - 4, ex + 5, EYE_Y + 4), start=20, end=160, fill=WHITE)
        elif mood == "happy":
            draw.arc((ex - 5, EYE_Y - 2, ex + 5, EYE_Y + 6), start=200, end=340, fill=WHITE)  # ^ ^
        elif mood == "awake" and t % 4.0 > 3.85:
            draw.line((ex - 4, EYE_Y, ex + 4, EYE_Y), fill=WHITE)  # blink
        elif mood in ("thinking", "searching", "confused"):
            # Outlined eye with the pupil looking somewhere.
            draw.ellipse((ex - 5, EYE_Y - 5, ex + 5, EYE_Y + 5), outline=WHITE, fill=BLACK)
            if mood == "thinking":
                dx, dy = 2, -2                                     # up and to the right
            elif mood == "searching":
                dx, dy = round(2 * math.sin(t * 3)), 0             # scanning side to side
            else:
                dx, dy = 0, 0
            draw.ellipse((ex + dx - 2, EYE_Y + dy - 2, ex + dx + 2, EYE_Y + dy + 2), fill=WHITE)
        else:
            r = 5 if mood in ("hearing", "alert") else 4           # wide eyes
            draw.ellipse((ex - r, EYE_Y - r, ex + r, EYE_Y + r), fill=WHITE)
            draw.point((ex + 1, EYE_Y - 2), fill=BLACK)            # sparkle
    if mood == "confused":
        draw.line((CX + 6, EYE_Y - 10, CX + 17, EYE_Y - 8), fill=WHITE)  # raised eyebrow


def _nose_and_mouth(draw, mood, speaking, frame):
    draw.polygon([(CX - 3, 46), (CX + 3, 46), (CX, 49)], fill=WHITE)
    if speaking and frame % 2 == 0:
        draw.ellipse((CX - 4, 50, CX + 4, 58), outline=WHITE, fill=BLACK)  # mouth open
        return
    draw.line((CX, 49, CX, 52), fill=WHITE)
    if mood == "confused":
        draw.line((CX - 5, 54, CX + 5, 52), fill=WHITE)                    # wonky mouth
    elif mood == "alert":
        draw.ellipse((CX - 2, 52, CX + 2, 56), outline=WHITE)              # "oh!"
    else:
        draw.arc((CX - 6, 48, CX, 55), start=0, end=150, fill=WHITE)
        draw.arc((CX, 48, CX + 6, 55), start=30, end=180, fill=WHITE)


def _whiskers(draw):
    for dy in (-2, 3):
        draw.line((CX - 10, 49 + dy // 2, CX - 34, 46 + dy * 2), fill=WHITE)
        draw.line((CX + 10, 49 + dy // 2, CX + 34, 46 + dy * 2), fill=WHITE)


def _extras(draw, mood, frame):
    if mood == "asleep":
        rise = frame // 3 % 6
        draw.text((CX + 34, 16 - rise), "z", fill=WHITE)
        draw.text((CX + 43, 8 - rise), "Z", fill=WHITE)
    elif mood == "thinking":
        draw.ellipse((CX + 31, 20, CX + 34, 23), outline=WHITE)
        draw.ellipse((CX + 35, 12, CX + 40, 17), outline=WHITE)
        draw.ellipse((CX + 38, 0, CX + 63, 12), outline=WHITE, fill=BLACK)
        for i in range(frame % 4):
            x = CX + 44 + i * 6
            draw.rectangle((x, 5, x + 1, 6), fill=WHITE)
    elif mood == "searching":
        x = CX + 42 + (0, 5, 10, 5)[frame % 4]
        draw.ellipse((x - 6, 2, x + 6, 14), outline=WHITE, fill=BLACK)    # magnifier lens
        draw.line((x + 4, 12, x + 9, 18), fill=WHITE, width=2)            # handle
        draw.text((CX - 62, 2), "www", fill=WHITE)
    elif mood == "hearing":
        # Sound waves rippling out beside each ear.
        for i in range(1 + frame % 3):
            r = 6 + i * 5
            draw.arc((CX - 22 - r, 14 - r, CX - 22 + r, 14 + r), start=140, end=220, fill=WHITE)
            draw.arc((CX + 22 - r, 14 - r, CX + 22 + r, 14 + r), start=320, end=40, fill=WHITE)
    elif mood == "happy":
        hx, hy = CX + 46, 6
        draw.ellipse((hx - 6, hy - 3, hx, hy + 3), fill=WHITE)
        draw.ellipse((hx, hy - 3, hx + 6, hy + 3), fill=WHITE)
        draw.polygon([(hx - 6, hy + 1), (hx + 6, hy + 1), (hx, hy + 9)], fill=WHITE)
    elif mood == "alert" and frame % 2 == 0:
        for x in (CX - 52, CX + 48):
            draw.rectangle((x, 2, x + 3, 16), fill=WHITE)                # "!"
            draw.rectangle((x, 20, x + 3, 23), fill=WHITE)
    elif mood == "confused":
        draw.text((CX + 40, 2), "?", fill=WHITE)
        draw.text((CX + 48, 10), "?", fill=WHITE)


def rabbit_image(mood="awake", speaking=False, t=0.0):
    """The face as a 1-bit PIL image (also handy for previewing it)."""
    from PIL import Image, ImageDraw

    image = Image.new("1", (WIDTH, HEIGHT), 0)
    draw_rabbit(ImageDraw.Draw(image), mood, speaking, t)
    return image


# ---------------------------------------------------------------- the screen
class RabbitDisplay:
    def __init__(self, device=None, animate=True):
        self._device = device
        self._lock = threading.Lock()
        self._base = "off"            # "off", "asleep" or "awake", from Rabbit's state
        self._active = {}             # mood -> how many `with mood()` blocks hold it
        self._until = {}              # flashed mood -> time.monotonic() when it ends
        self._drawn = None            # bytes of the last frame sent, to skip repeats
        self._stop = threading.Event()
        self._thread = None
        if device is not None and animate:
            self._thread = threading.Thread(target=self._run, name="oled", daemon=True)
            self._thread.start()

    # ------------------------------------------------ what the program calls
    def show(self, on, awake=True):
        """Rabbit's state: face when `on` (eyes open if `awake`), blank otherwise."""
        with self._lock:
            self._base = ("awake" if awake else "asleep") if on else "off"
        self.refresh()

    @contextmanager
    def mood(self, name):
        """Shows `name` for the duration of a `with` block."""
        with self._lock:
            self._active[name] = self._active.get(name, 0) + 1
        self.refresh()
        try:
            yield
        finally:
            with self._lock:
                self._active[name] -= 1
            self.refresh()

    def flash(self, name, seconds=None):
        """Shows `name` for a moment ("happy" when a fact is saved)."""
        with self._lock:
            self._until[name] = time.monotonic() + (seconds or FLASH_SECONDS.get(name, 2.0))
        self.refresh()

    def current(self, now=None):
        """(mood, speaking) the face should show right now."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._base == "off":
                return "off", False
            live = {m for m, n in self._active.items() if n > 0}
            live |= {m for m, end in self._until.items() if end > now}
            base = self._base
        if base == "asleep":
            live &= ASLEEP_MOODS
        mood = next((m for m in PRIORITY if m in live), base)
        return mood, "speaking" in live

    # ------------------------------------------------ drawing
    def refresh(self):
        """Draws the current face now (the thread also does this for animations)."""
        if self._device is None:
            return
        mood, speaking = self.current()
        try:
            if mood == "off":
                if self._drawn != "off":
                    self._device.hide()
                    self._drawn = "off"
                return
            image = rabbit_image(mood, speaking, time.monotonic())
            data = image.tobytes()
            if data != self._drawn:
                self._device.display(image.convert(self._device.mode))
                if self._drawn in (None, "off"):
                    self._device.show()
                self._drawn = data
        except Exception as e:
            log.warning("OLED update failed: %s", e)

    def _run(self):
        while not self._stop.wait(FRAME_SECONDS):
            self.refresh()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self._device is not None:
            try:
                self._device.cleanup()  # clears the screen
            except Exception:
                pass
        self._device = None


def open_display(enabled=True):
    """The OLED on I2C, or a do-nothing display if it isn't there."""
    if not enabled or not config.OLED_DRIVER:
        return RabbitDisplay()
    try:
        from luma.core.interface.serial import i2c
        from luma.oled import device as oled_devices

        driver = getattr(oled_devices, config.OLED_DRIVER)  # ssd1306 or sh1106
        device = driver(i2c(port=1, address=config.OLED_ADDRESS), width=WIDTH, height=HEIGHT)
    except Exception as e:
        log.warning("No OLED screen (%s); running without it", e)
        return RabbitDisplay()
    log.info("OLED %s at I2C address 0x%02X", config.OLED_DRIVER, config.OLED_ADDRESS)
    return RabbitDisplay(device)


# The one display the whole program uses; main.py replaces it at startup.
screen = RabbitDisplay()


if __name__ == "__main__":
    # Preview without a screen: python display.py -> rabbit_faces.png
    from PIL import Image

    moods = ["awake", "asleep", "hearing", "thinking", "searching", "happy", "alert", "confused"]
    sheet = Image.new("1", (WIDTH * 4 + 12, (HEIGHT + 4) * 2 + 4 + HEIGHT + 4), 1)
    for i, mood in enumerate(moods):
        t = 2 * FRAME_SECONDS + 0.01  # a frame where every animation is showing something
        sheet.paste(rabbit_image(mood, t=t), (4 + (i % 4) * (WIDTH + 1), 4 + (i // 4) * (HEIGHT + 4)))
    sheet.paste(rabbit_image("awake", speaking=True, t=0.0), (4, 4 + 2 * (HEIGHT + 4)))
    sheet.resize((sheet.width * 2, sheet.height * 2)).save("rabbit_faces.png")
    print("saved rabbit_faces.png: " + ", ".join(moods) + ", speaking")
