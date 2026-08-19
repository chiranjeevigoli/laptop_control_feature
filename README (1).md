# Hand Gesture Laptop Control (Windows)

Control your laptop's volume, mute, window switching, and app open/close
using just your webcam and hand gestures — no extra hardware needed.

## What it does

| Gesture | Action |
|---|---|
| Pinch thumb + index finger (other fingers curled) | Adjust system volume — closer = quieter, farther = louder |
| Make a fist and hold briefly | Toggle mute |
| Open palm, swipe hand left/right | Switch windows (Alt+Tab / Alt+Shift+Tab) |
| Index + middle finger up ("peace"), hold ~1.5s | Open a configured app |
| Thumbs-down, hold ~1.5s | Close the active window (Alt+F4) |

## Setup

1. **Install Python 3.10** (recommended — MediaPipe support is most reliable
   on 3.9–3.11). Get it from python.org and make sure "Add to PATH" is checked
   during install.

2. **Open Command Prompt** in this folder (`hand-control`) and install
   dependencies:
   ```
   pip install -r requirements.txt
   ```

3. **Configure the app to open.** Open `gesture_control.py` in any text
   editor and edit this line near the top:
   ```python
   APP_TO_OPEN = "notepad.exe"
   ```
   Change it to any app you want, e.g.:
   ```python
   APP_TO_OPEN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
   ```

4. **Run it:**
   ```
   python gesture_control.py
   ```
   A window will pop up showing your webcam feed with hand tracking overlay
   and the current detected action.

5. **Quit** by clicking the video window and pressing `q`.

## Tips for reliable tracking

- Sit somewhere with decent, even lighting (avoid strong backlight).
- Keep your hand fully inside the frame, roughly 30–60cm from the camera.
- Move gesture-to-gesture with a brief pause — the script uses a short
  cooldown between actions to avoid accidental double-triggers.
- If gestures feel too sensitive or not sensitive enough, tweak these
  constants near the top of the script:
  - `HOLD_SECONDS` — how long to hold "open app" / "close app" gestures
  - `ACTION_COOLDOWN` — minimum time between two discrete actions
  - `dist_min` / `dist_max` in `set_volume_from_pinch_distance` — pinch
    distance range mapped to 0–100% volume

## Extending it

This script is intentionally simple so you can build on it:

- **Add more gestures** — write a new `is_your_gesture(fingers)` helper
  using the `fingers_up()` boolean list `[thumb, index, middle, ring, pinky]`,
  then check it in the main loop like the existing gestures.
- **Add two-hand gestures** — change `max_num_hands=1` to `2` in
  `mp_hands.Hands(...)` and loop over `result.multi_hand_landmarks`.
- **Map gestures to specific apps** — instead of one `APP_TO_OPEN`, use a
  dictionary of gesture → app path, and launch based on which gesture fired.
- **Brightness/media controls** — `pyautogui` can send media keys too, e.g.
  `pyautogui.press("volumemute")`, `pyautogui.press("playpause")`.

## Troubleshooting

- **"Could not open webcam"** — another app (Zoom, Teams, camera app) may be
  using it. Close those, or change `CAMERA_INDEX` to `1` if you have multiple
  cameras.
- **pycaw / comtypes install errors** — make sure you're on Windows (this
  volume-control approach is Windows-specific) and try
  `pip install --upgrade pip` first.
- **Hand not detected** — improve lighting, move closer, and make sure your
  full hand (not just fingertips) is in frame.
- **Volume feels jumpy** — narrow the `dist_min`/`dist_max` range in
  `set_volume_from_pinch_distance` to match your comfortable pinch range.
