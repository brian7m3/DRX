import time
import RPi.GPIO as GPIO

REMOTE_BUSY_PIN = 13  # Use your actual pin
REMOTE_BUSY_ACTIVE_LEVEL = True  # Or False, as appropriate

GPIO.setmode(GPIO.BCM)
GPIO.setup(REMOTE_BUSY_PIN, GPIO.OUT)

for i in range(3):
    print(f"Playback {i+1}: RDB ON")
    GPIO.output(REMOTE_BUSY_PIN, REMOTE_BUSY_ACTIVE_LEVEL)
    time.sleep(2)  # Simulate playback
    print(f"Playback {i+1}: RDB OFF")
    GPIO.output(REMOTE_BUSY_PIN, not REMOTE_BUSY_ACTIVE_LEVEL)
    time.sleep(1)  # Simulate pause/wait

GPIO.cleanup()