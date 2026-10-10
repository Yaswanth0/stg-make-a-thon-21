#!/usr/bin/env bash
# Makes Rabbit start by itself when the Raspberry Pi is switched on.
#
#   bash install_autostart.sh            install (or update) and start it now
#   bash install_autostart.sh --remove   stop it and turn auto-start off
#
# Run it as your normal user (not with sudo); it asks for your password once.
# It installs a systemd *user* service, so Rabbit runs as you, with your
# PipeWire audio, Bluetooth speaker, GPIO and venv.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/companion.service"

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user, without sudo." >&2
    exit 1
fi

if [[ "${1:-}" == "--remove" ]]; then
    systemctl --user disable --now companion 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload
    echo "Auto-start removed. (Lingering stays on; turn it off with: sudo loginctl disable-linger $USER)"
    exit 0
fi

# ---------------------------------------------------------------- Python
if [[ -x "$DIR/venv/bin/python" ]]; then
    PY="$DIR/venv/bin/python"
elif [[ -x "$DIR/../venv/bin/python" ]]; then
    PY="$(cd "$DIR/.." && pwd)/venv/bin/python"
else
    PY="$(command -v python3)"
    echo "No venv found next to the project; using $PY"
fi
echo "Project: $DIR"
echo "Python:  $PY"

# ---------------------------------------------------------------- the service
mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<EOF
# Made by install_autostart.sh - run it again after moving the project.
[Unit]
Description=Rabbit voice companion
# Audio must be up first: Rabbit speaks through PipeWire (Bluetooth speaker).
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service wireplumber.service

[Service]
WorkingDirectory=$DIR
# A few seconds for the Bluetooth speaker to reconnect after boot, so
# "System ready" comes out of it rather than the HDMI/headphone output.
ExecStartPre=/bin/sleep 10
ExecStart=$PY $DIR/main.py
Environment=PYTHONUNBUFFERED=1
# A crash restarts Rabbit. "Mayday" exits cleanly and stays stopped until the
# next boot (or: systemctl --user start companion).
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
echo "Wrote $UNIT"

systemctl --user daemon-reload
systemctl --user enable companion

# Start the user's services at boot, without anyone logging in.
if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
    echo "Enabling start at boot without login (needs your password):"
    sudo loginctl enable-linger "$USER"
fi

# ---------------------------------------------------------------- Ollama
if systemctl list-unit-files ollama.service >/dev/null 2>&1; then
    if [[ "$(systemctl is-enabled ollama 2>/dev/null)" != "enabled" ]]; then
        echo "Making Ollama start at boot too:"
        sudo systemctl enable --now ollama
    fi
else
    echo "WARNING: no ollama.service found. Rabbit needs Ollama running at boot."
fi

# ---------------------------------------------------------------- permissions
missing=()
for group in gpio audio i2c; do
    if ! id -nG "$USER" | tr ' ' '\n' | grep -qx "$group"; then
        missing+=("$group")
    fi
done
if (( ${#missing[@]} )); then
    echo "WARNING: $USER is not in: ${missing[*]} (switch, LEDs, mic or OLED may fail)."
    echo "         Fix: sudo usermod -aG $(IFS=,; echo "${missing[*]}") $USER   then reboot."
fi

# ---------------------------------------------------------------- start now
# A copy started by hand in a terminal (not the service's own) would fight the
# service over the mic, speaker and GPIO.
service_pid="$(systemctl --user show -p MainPID --value companion 2>/dev/null || echo 0)"
for pid in $(pgrep -u "$USER" -f "main.py" 2>/dev/null || true); do
    if [[ "$pid" != "$service_pid" ]] && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "python"; then
        echo "NOTE: Rabbit is also running in a terminal (process $pid). Stop it with Ctrl+C;"
        echo "      two copies would fight over the mic, speaker and GPIO."
        break
    fi
done
systemctl --user restart companion
sleep 2
systemctl --user --no-pager --lines=0 status companion || true

cat <<EOF

Done. Rabbit now starts by itself about 10-20 seconds after the Pi boots.

  Watch it:     journalctl --user -u companion -f
  Stop it:      systemctl --user stop companion
  Start it:     systemctl --user start companion
  After a git pull:  systemctl --user restart companion
  Turn off auto-start:  bash install_autostart.sh --remove
EOF
