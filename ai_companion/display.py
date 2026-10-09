"""OLED screen (128x64, I2C): shows a rabbit face while Rabbit is on.

    RUNNING  rabbit with open eyes
    SLEEP    rabbit with closed eyes
    OFF / exited  blank screen

Uses luma.oled (pip install luma.oled) and I2C, which must be enabled once:
sudo raspi-config -> Interface Options -> I2C. Without the library or the
screen, every call does nothing, so the rest of the program doesn't care.
"""

import logging
import threading

import config

log = logging.getLogger("display")

WIDTH, HEIGHT = 128, 64


def draw_rabbit(draw, awake=True):
    """Draws the rabbit face on a 128x64 PIL ImageDraw (white on black)."""
    white, black = 255, 0
    cx = WIDTH // 2

    # Ears first; the head is drawn over their bottoms.
    for x in (cx - 17, cx + 5):
        draw.ellipse((x, 0, x + 12, 30), outline=white, fill=black)
        draw.ellipse((x + 4, 5, x + 8, 24), fill=white)              # inner ear

    # Head
    draw.ellipse((cx - 30, 19, cx + 30, 63), outline=white, fill=black)

    # Eyes
    for ex in (cx - 12, cx + 12):
        if awake:
            draw.ellipse((ex - 4, 33, ex + 4, 41), fill=white)
            draw.point((ex + 1, 35), fill=black)                     # sparkle
        else:
            draw.arc((ex - 5, 33, ex + 5, 41), start=20, end=160, fill=white)  # closed, smiling

    # Nose and mouth
    draw.polygon([(cx - 3, 46), (cx + 3, 46), (cx, 49)], fill=white)
    draw.line((cx, 49, cx, 52), fill=white)
    draw.arc((cx - 6, 48, cx, 55), start=0, end=150, fill=white)
    draw.arc((cx, 48, cx + 6, 55), start=30, end=180, fill=white)

    # Whiskers
    for dy in (-2, 3):
        draw.line((cx - 10, 49 + dy // 2, cx - 34, 46 + dy * 2), fill=white)
        draw.line((cx + 10, 49 + dy // 2, cx + 34, 46 + dy * 2), fill=white)

    # Sleeping: a little "z"
    if not awake:
        draw.text((cx + 34, 14), "z", fill=white)
        draw.text((cx + 42, 6), "Z", fill=white)


def rabbit_image(awake=True):
    """The face as a 1-bit PIL image (also handy for previewing it)."""
    from PIL import Image, ImageDraw

    image = Image.new("1", (WIDTH, HEIGHT), 0)
    draw_rabbit(ImageDraw.Draw(image), awake)
    return image


class RabbitDisplay:
    def __init__(self, device=None):
        self._device = device
        self._lock = threading.Lock()
        self._showing = None  # "awake", "asleep" or "off"

    def show(self, on, awake=True):
        """Rabbit face when `on` (eyes open if `awake`), blank screen otherwise."""
        wanted = ("awake" if awake else "asleep") if on else "off"
        with self._lock:
            if self._device is None or wanted == self._showing:
                return
            try:
                if on:
                    self._device.display(rabbit_image(awake).convert(self._device.mode))
                    self._device.show()
                else:
                    self._device.hide()  # screen off: blank and saves the OLED
                self._showing = wanted
            except Exception as e:
                log.warning("OLED update failed: %s", e)

    def close(self):
        with self._lock:
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
    # Preview without a screen: python display.py -> rabbit_awake.png, rabbit_asleep.png
    for awake, name in ((True, "awake"), (False, "asleep")):
        rabbit_image(awake).resize((WIDTH * 4, HEIGHT * 4)).save(f"rabbit_{name}.png")
        print(f"saved rabbit_{name}.png")
