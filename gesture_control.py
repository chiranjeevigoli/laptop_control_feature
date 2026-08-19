"""
Hand Gesture Control for Windows
---------------------------------
Uses your webcam + MediaPipe hand tracking to control:
  - System volume (pinch thumb+index finger)
  - Mute (make a fist)
  - Switch windows / Alt-Tab (swipe hand left/right)
  - Open an app (two fingers up, held for 1.5s)
  - Close active app (thumbs-down held for 1.5s)

RUN THIS ON YOUR OWN WINDOWS LAPTOP (not in a browser/sandbox) -
it needs direct access to your webcam and OS.

SETUP (one-time):
    1. Install Python 3.9-3.11 (MediaPipe doesn't yet support very new
       Python versions on all systems - 3.10 is a safe bet).
    2. Open Command Prompt in this folder and run:
           pip install -r requirements.txt
    3. Edit the APP_TO_OPEN path below to an app you actually have.
    4. Run:
           python gesture_control.py
    5. Press 'q' with the video window focused to quit.

TIP: Good lighting and keeping your hand ~30-60cm from the camera,
fully in frame, gives the most reliable tracking.
"""

import time
import subprocess
import math

import cv2
import mediapipe as mp
import pyautogui

from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

# ----------------------------- CONFIG ---------------------------------

# Path (or command) to launch when the "open app" gesture fires.
# Examples:
#   Notepad:  "notepad.exe"
#   Chrome:   r"C:\Program Files\Google\Chrome\Application\chrome.exe"
#   Calculator: "calc.exe"
APP_TO_OPEN = "notepad.exe"

# How many seconds a "hold" gesture (open app / close app) must be
# sustained before it triggers, to avoid accidental firing.
HOLD_SECONDS = 1.5

# Cooldown between repeated discrete actions (mute toggle, swipe, open/close)
ACTION_COOLDOWN = 1.0

# Webcam index - 0 is usually the built-in camera
CAMERA_INDEX = 0

# ------------------------------------------------------------------------

mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils

hands = mp_hands.Hands(
    max_num_hands=1,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.6,
)

# --- Windows volume control setup (pycaw) ---
devices = AudioUtilities.GetSpeakers()
interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
volume_ctrl = cast(interface, POINTER(IAudioEndpointVolume))
VOL_MIN, VOL_MAX = volume_ctrl.GetVolumeRange()[:2]  # in dB, e.g. -65.25 to 0


def set_volume_from_pinch_distance(dist, dist_min=20, dist_max=200):
    """Map pinch distance (pixels) to system volume (dB range)."""
    dist = max(dist_min, min(dist_max, dist))
    vol_scalar = (dist - dist_min) / (dist_max - dist_min)  # 0..1
    vol_db = VOL_MIN + vol_scalar * (VOL_MAX - VOL_MIN)
    volume_ctrl.SetMasterVolumeLevel(vol_db, None)
    return int(vol_scalar * 100)


def toggle_mute():
    is_muted = volume_ctrl.GetMute()
    volume_ctrl.SetMute(0 if is_muted else 1, None)
    return not is_muted


def landmark_pixel(landmark, w, h):
    return int(landmark.x * w), int(landmark.y * h)


def fingers_up(hand_landmarks, w, h):
    """Return list of 5 booleans: [thumb, index, middle, ring, pinky] up or not."""
    lm = hand_landmarks.landmark
    fingers = []

    # Thumb: compare x of tip vs joint (works for a right hand facing camera)
    fingers.append(lm[4].x < lm[3].x)

    # Other 4 fingers: tip above (smaller y) than the pip joint = "up"
    tips = [8, 12, 16, 20]
    pips = [6, 10, 14, 18]
    for tip, pip in zip(tips, pips):
        fingers.append(lm[tip].y < lm[pip].y)

    return fingers  # [thumb, index, middle, ring, pinky]


def is_fist(fingers):
    return not any(fingers)


def is_two_fingers_up(fingers):
    # index + middle up, others down (a "peace"/scissors-ish shape without spread check)
    return fingers[1] and fingers[2] and not fingers[3] and not fingers[4]


def is_thumbs_down(hand_landmarks, fingers):
    lm = hand_landmarks.landmark
    thumb_tip_y = lm[4].y
    wrist_y = lm[0].y
    # thumb pointing down and other fingers curled
    only_thumb_extended = fingers[1] is False and fingers[2] is False and fingers[3] is False and fingers[4] is False
    return only_thumb_extended and thumb_tip_y > wrist_y


def close_active_window():
    pyautogui.hotkey("alt", "f4")


def open_app():
    subprocess.Popen(APP_TO_OPEN)


def main():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Could not open webcam. Check CAMERA_INDEX or camera permissions.")
        return

    prev_x = None
    last_action_time = 0.0
    fist_hold_start = None
    two_finger_hold_start = None
    thumbs_down_hold_start = None
    last_volume_pct = None

    print("Hand gesture control running. Press 'q' in the video window to quit.")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        frame = cv2.flip(frame, 1)  # mirror for natural control
        h, w, _ = frame.shape
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = hands.process(rgb)

        now = time.time()
        status_text = ""

        if result.multi_hand_landmarks:
            hand_landmarks = result.multi_hand_landmarks[0]
            mp_draw.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)

            lm = hand_landmarks.landmark
            fingers = fingers_up(hand_landmarks, w, h)

            thumb_px = landmark_pixel(lm[4], w, h)
            index_px = landmark_pixel(lm[8], w, h)
            wrist_px = landmark_pixel(lm[0], w, h)

            pinch_dist = math.hypot(thumb_px[0] - index_px[0], thumb_px[1] - index_px[1])

            # --- Gesture: pinch -> volume (only when just thumb+index extended-ish) ---
            if fingers[0] and fingers[1] and not fingers[2] and not fingers[3] and not fingers[4]:
                vol_pct = set_volume_from_pinch_distance(pinch_dist)
                if vol_pct != last_volume_pct:
                    last_volume_pct = vol_pct
                status_text = f"Volume: {vol_pct}%"
                cv2.line(frame, thumb_px, index_px, (0, 255, 0), 3)

            # --- Gesture: fist -> mute toggle ---
            elif is_fist(fingers):
                if fist_hold_start is None:
                    fist_hold_start = now
                elif now - fist_hold_start > 0.4 and now - last_action_time > ACTION_COOLDOWN:
                    muted = toggle_mute()
                    status_text = "Muted" if muted else "Unmuted"
                    last_action_time = now
                    fist_hold_start = None
            else:
                fist_hold_start = None

            # --- Gesture: two fingers up held -> open app ---
            if is_two_fingers_up(fingers):
                if two_finger_hold_start is None:
                    two_finger_hold_start = now
                elif now - two_finger_hold_start > HOLD_SECONDS and now - last_action_time > ACTION_COOLDOWN:
                    open_app()
                    status_text = f"Opening {APP_TO_OPEN}"
                    last_action_time = now
                    two_finger_hold_start = None
            else:
                two_finger_hold_start = None

            # --- Gesture: thumbs down held -> close active window ---
            if is_thumbs_down(hand_landmarks, fingers):
                if thumbs_down_hold_start is None:
                    thumbs_down_hold_start = now
                elif now - thumbs_down_hold_start > HOLD_SECONDS and now - last_action_time > ACTION_COOLDOWN:
                    close_active_window()
                    status_text = "Closing active window"
                    last_action_time = now
                    thumbs_down_hold_start = None
            else:
                thumbs_down_hold_start = None

            # --- Gesture: open palm swipe left/right -> alt-tab style window switch ---
            open_palm = all(fingers)
            if open_palm:
                if prev_x is not None:
                    dx = wrist_px[0] - prev_x
                    if abs(dx) > 80 and now - last_action_time > ACTION_COOLDOWN:
                        if dx > 0:
                            pyautogui.hotkey("alt", "tab")
                            status_text = "Swipe right: Alt+Tab"
                        else:
                            pyautogui.hotkey("alt", "shift", "tab")
                            status_text = "Swipe left: Alt+Shift+Tab"
                        last_action_time = now
                prev_x = wrist_px[0]
            else:
                prev_x = None

        # UI overlay
        cv2.putText(frame, status_text, (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(frame, "Press 'q' to quit", (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        cv2.imshow("Hand Gesture Control", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
