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
    # Check if the MediaPipe model file is already present in the folder.
    # If it's missing, download it from the official Google URL so we can run hand tracking.
    if not os.path.exists(MODEL_PATH):
        print(f"Downloading hand landmarker model from {MODEL_URL}...")
        try:
            urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
            print("Download complete.")
        except Exception as e:
            print(f"Error downloading model: {e}")
            raise

# --- Windows volume control setup (pycaw) ---
# Initialize the Pycaw audio library to interact with the main audio speaker endpoint.
volume_ctrl = AudioUtilities.GetSpeakers().EndpointVolume
# Retrieve the minimum and maximum volume range in decibels (dB), which varies by system hardware.
VOL_MIN, VOL_MAX = volume_ctrl.GetVolumeRange()[:2]  # in dB, e.g. -65.25 to 0


def set_volume_from_pinch_distance(dist, dist_min=20, dist_max=200):
    """Map pinch distance (pixels) to system volume (dB range)."""
    # Constrain the measured pixel distance between the minimum and maximum expected bounds.
    dist = max(dist_min, min(dist_max, dist))
    # Calculate a normalized ratio (0.0 to 1.0) indicating how far apart the fingers are.
    vol_scalar = (dist - dist_min) / (dist_max - dist_min)  # 0..1
    # Convert this ratio to a decibel level fitting within the system volume range.
    vol_db = VOL_MIN + vol_scalar * (VOL_MAX - VOL_MIN)
    # Apply the calculated volume level to the Windows system output.
    volume_ctrl.SetMasterVolumeLevel(vol_db, None)
    # Return the volume as a percentage integer (0 to 100) for display.
    return int(vol_scalar * 100)


def toggle_mute():
    # Query the current mute state of the audio controller.
    is_muted = volume_ctrl.GetMute()
    # If currently muted, unmute it (0); if unmuted, mute it (1).
    volume_ctrl.SetMute(0 if is_muted else 1, None)
    # Return the new mute state (True if muted, False if unmuted).
    return not is_muted


def landmark_pixel(landmark, w, h):
    # MediaPipe returns normalized coordinates (0.0 to 1.0).
    # Convert these normalized values back into actual screen pixel positions (X and Y).
    return int(landmark.x * w), int(landmark.y * h)


def fingers_up(hand_landmarks, w, h):
    """Return list of 5 booleans: [thumb, index, middle, ring, pinky] up or not."""
    lm = hand_landmarks
    fingers = []

    # Check the thumb: we compare the x-coordinate of the thumb tip (landmark 4)
    # with the inner joint (landmark 3). Note: this basic heuristic assumes
    # a right hand facing the camera.
    fingers.append(lm[4].x < lm[3].x)

    # Check the remaining four fingers (index, middle, ring, pinky).
    # We compare the y-coordinate of the tip with the PIP joint (first joint below the tip).
    # Since the origin (0,0) is at the top-left, a smaller y-coordinate means the tip is higher up.
    tips = [8, 12, 16, 20]
    pips = [6, 10, 14, 18]
    for tip, pip in zip(tips, pips):
        fingers.append(lm[tip].y < lm[pip].y)

    return fingers  # [thumb, index, middle, ring, pinky]


def is_fist(fingers):
    # If all fingers are curled down (none are "up"), we define this as a fist.
    return not any(fingers)


def is_two_fingers_up(fingers):
    # Check if only the index and middle fingers are extended (like a peace sign),
    # while the thumb, ring, and pinky fingers are folded down.
    return fingers[1] and fingers[2] and not fingers[3] and not fingers[4]


def is_thumbs_down(hand_landmarks, fingers):
    lm = hand_landmarks
    thumb_tip_y = lm[4].y
    wrist_y = lm[0].y
    # Check if only the thumb is extended and all other four fingers are curled in.
    only_thumb_extended = fingers[1] is False and fingers[2] is False and fingers[3] is False and fingers[4] is False
    # If the thumb is the only extended finger, and the thumb tip's y-coordinate is below
    # the wrist (meaning it points downward physically), trigger thumbs down.
    return only_thumb_extended and thumb_tip_y > wrist_y


def close_active_window():
    # Use PyAutoGUI to simulate the system shortcut Alt+F4, which closes the active window.
    pyautogui.hotkey("alt", "f4")


def open_app():
    # Launch the configured application in the background without blocking execution.
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
    # Get the width and height of the video frame.
    h, w, _ = frame.shape
    
    # Iterate through the predefined connections list to draw lines between joints.
    for connection in HAND_CONNECTIONS:
        start_idx, end_idx = connection
        if start_idx < len(hand_landmarks) and end_idx < len(hand_landmarks):
            # Calculate pixel positions for the start and end landmarks of the connection.
            start_point = landmark_pixel(hand_landmarks[start_idx], w, h)
            end_point = landmark_pixel(hand_landmarks[end_idx], w, h)
            # Draw a green connecting line with a thickness of 2 pixels.
            cv2.line(frame, start_point, end_point, (0, 255, 0), 2)
            
    # Loop through each individual landmark point on the hand.
    for lm in hand_landmarks:
        # Convert its normalized coordinates to pixel coordinates.
        px = landmark_pixel(lm, w, h)
        # Draw a solid red circle at each landmark location.
        cv2.circle(frame, px, 5, (0, 0, 255), -1)


def main():
    # Make sure we have the MediaPipe model file locally before doing anything else.
    download_model_if_missing()

    # Open the webcam stream using OpenCV.
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("Could not open webcam. Check CAMERA_INDEX or camera permissions.")
        return

    # Set up base options for the MediaPipe HandLandmarker API.
    base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
    # We run in VIDEO mode since we are processing a live webcam stream frame-by-frame.
    options = vision.HandLandmarkerOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1
    )

    # Initialize tracking variables for gesture durations and coordinates.
    prev_x = None                  # Keeps track of wrist X position for swipe gestures
    last_action_time = 0.0         # Cooldown timer to prevent gestures from firing repeatedly
    fist_hold_start = None         # Timer to track how long a fist is held (mute toggle)
    two_finger_hold_start = None   # Timer to track how long two fingers are held up (open app)
    thumbs_down_hold_start = None  # Timer to track how long thumbs-down is held (close window)
    last_volume_pct = None         # Cached volume percentage to avoid redundant prints

    print("Hand gesture control running. Press 'q' in the video window to quit.")

    # Create the MediaPipe hand detector instance.
    with vision.HandLandmarker.create_from_options(options) as landmarker:
        while True:
            # Read a frame from the webcam.
            ok, frame = cap.read()
            if not ok:
                break

            # Mirror the image horizontally so moving left/right matches screen movement.
            frame = cv2.flip(frame, 1)  
            h, w, _ = frame.shape
            # MediaPipe requires RGB format, while OpenCV uses BGR by default.
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Wrap the image data into MediaPipe's custom image structure.
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            
            # MediaPipe's video mode requires a monotonically increasing millisecond timestamp.
            timestamp_ms = int(time.time() * 1000)
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            now = time.time()
            status_text = ""

            # Check if any hands were detected in the frame.
            if result.hand_landmarks:
                # We configured MediaPipe to only track one hand, so grab the first result.
                hand_landmarks = result.hand_landmarks[0]
                # Draw the hand joints and connections on the screen.
                draw_landmarks(frame, hand_landmarks)

                lm = hand_landmarks
                # Get the state of each finger (extended or curled).
                fingers = fingers_up(hand_landmarks, w, h)

                # Get pixel coordinates for key joints (thumb tip, index tip, and wrist).
                thumb_px = landmark_pixel(lm[4], w, h)
                index_px = landmark_pixel(lm[8], w, h)
                wrist_px = landmark_pixel(lm[0], w, h)

                # Calculate straight-line distance between thumb tip and index tip.
                pinch_dist = math.hypot(thumb_px[0] - index_px[0], thumb_px[1] - index_px[1])

                # --- Gesture: pinch -> volume (only when just thumb+index extended-ish) ---
                # Checks if only thumb and index are up, while middle, ring, and pinky are closed.
                if fingers[0] and fingers[1] and not fingers[2] and not fingers[3] and not fingers[4]:
                    vol_pct = set_volume_from_pinch_distance(pinch_dist)
                    if vol_pct != last_volume_pct:
                        last_volume_pct = vol_pct
                    status_text = f"Volume: {vol_pct}%"
                    # Draw a line connecting the thumb and index finger to show the pinch.
                    cv2.line(frame, thumb_px, index_px, (0, 255, 0), 3)

                # --- Gesture: fist -> mute toggle ---
                # Checks if a fist is detected. We require a brief hold (0.4s) to avoid false triggers.
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
                # Checks if index and middle are raised, and holds it for HOLD_SECONDS to launch the app.
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
                # Checks for thumbs down. If held for HOLD_SECONDS, sends Alt+F4 to close the active window.
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
                # When all fingers are extended, track wrist movement horizontally to register a swipe.
                open_palm = all(fingers)
                if open_palm:
                    if prev_x is not None:
                        dx = wrist_px[0] - prev_x
                        # If hand moved fast enough horizontally (> 80 pixels) and cooldown passed, trigger action.
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

            # Render status text overlay on the top left of the video frame.
            cv2.putText(frame, status_text, (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            # Render quit instructions at the bottom left of the video frame.
            cv2.putText(frame, "Press 'q' to quit", (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

            # Display the resulting frame in a window.
            cv2.imshow("Hand Gesture Control", frame)
            # Break the loop if the user presses the 'q' key.
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    # Release webcam and close OpenCV window handles.
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
