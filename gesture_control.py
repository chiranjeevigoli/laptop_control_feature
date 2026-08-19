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
import os
import urllib.request

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
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

# Model File Path & Download URL for MediaPipe Tasks API
MODEL_PATH = "hand_landmarker.task"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"

# ------------------------------------------------------------------------

def download_model_if_missing():
    if not os.path.exists(MODEL_PATH):
        print(f"Downloading hand landmarker model from {MODEL_URL}...")
        try:
            urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
            print("Download complete.")
        except Exception as e:
            print(f"Error downloading model: {e}")
            raise

# --- Windows volume control setup (pycaw) ---
volume_ctrl = AudioUtilities.GetSpeakers().EndpointVolume
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
    lm = hand_landmarks
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
    lm = hand_landmarks
    thumb_tip_y = lm[4].y
    wrist_y = lm[0].y
    # thumb pointing down and other fingers curled
    only_thumb_extended = fingers[1] is False and fingers[2] is False and fingers[3] is False and fingers[4] is False
    return only_thumb_extended and thumb_tip_y > wrist_y


def close_active_window():
    pyautogui.hotkey("alt", "f4")


def open_app():
    subprocess.Popen(APP_TO_OPEN)


HAND_CONNECTIONS = [
    # Thumb
    (0, 1), (1, 2), (2, 3), (3, 4),
    # Index
    (0, 5), (5, 6), (6, 7), (7, 8),
    # Middle
    (5, 9), (9, 10), (10, 11), (11, 12),
    # Ring
    (9, 13), (13, 14), (14, 15), (15, 16),
    # Pinky
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17)
]


def draw_landmarks(frame, hand_landmarks):
    h, w, _ = frame.shape
    # Draw connections
    for connection in HAND_CONNECTIONS:
        start_idx, end_idx = connection
        if start_idx < len(hand_landmarks) and end_idx < len(hand_landmarks):
            start_point = landmark_pixel(hand_landmarks[start_idx], w, h)
            end_point = landmark_pixel(hand_landmarks[end_idx], w, h)
            cv2.line(frame, start_point, end_point, (0, 255, 0), 2)
            
    # Draw landmark points
    for lm in hand_landmarks:
        px = landmark_pixel(lm, w, h)
        cv2.circle(frame, px, 5, (0, 0, 255), -1)


def main():
    download_model_if_missing()

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Could not open webcam. Check CAMERA_INDEX or camera permissions.")
        return

    # Configure HandLandmarker
    base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
    options = vision.HandLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1
    )

    prev_x = None
    last_action_time = 0.0
    fist_hold_start = None
    two_finger_hold_start = None
    thumbs_down_hold_start = None
    last_volume_pct = None

    print("Hand gesture control running. Press 'q' in the video window to quit.")

    with vision.HandLandmarker.create_from_options(options) as landmarker:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            frame = cv2.flip(frame, 1)  # mirror for natural control
            h, w, _ = frame.shape
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # In VIDEO running mode, we need to create mp.Image and pass timestamp_ms
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            
            timestamp_ms = int(time.time() * 1000)
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            now = time.time()
            status_text = ""

            if result.hand_landmarks:
                hand_landmarks = result.hand_landmarks[0]
                draw_landmarks(frame, hand_landmarks)

                lm = hand_landmarks
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
