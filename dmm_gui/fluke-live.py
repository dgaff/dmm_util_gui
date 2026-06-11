#!/usr/bin/env python3
"""
fluke-live.py

Live view of Fluke 289/287 LCD screenshot using matplotlib.
Captures continuously and updates the display.
Quit with Ctrl+C or window close button.

Attributed to vsilves on eevblog
https://www.eevblog.com/forum/reviews/going-further-with-the-fluke-289/25/

NOTE: This is a standalone script for live screen capture, not part of the main app. It's included here to give credit to the original author and to show how the 'qlcdbm' 
command is used for live screen capture. The main app's Screen tab uses the same command but with a different UI and error handling.
"""

import glob, sys, time, serial, gzip, io
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from PIL import Image

def find_port():
    ports = glob.glob('/dev/cu.usbserial-*')
    if not ports:
        print("No Fluke serial port found.", file=sys.stderr)
        sys.exit(1)
    return ports[0]

def capture_screenshot(ser):
    chunks = []
    offset = 0
    cumulative_bytes = 0

    while True:
        cmd = f"QLCDBM {offset}\r"
        ser.write(cmd.encode('ascii'))
        ser.flush()

        data = b''
        timeout = time.time() + 0.15 # total timeout per request
        last_data = time.time()

        while time.time() < timeout:
            if ser.in_waiting > 0:
                chunk = ser.read(4096)
                data += chunk
                last_data = time.time()
            else:
                if time.time() - last_data > 0.15:
                    break
                time.sleep(0.01)

        if data.endswith(b'\r'):
            data = data[:-1]

        offset_str = str(offset)
        prefix = b'0\r' + offset_str.encode() + b' #0'
        if data.startswith(prefix):
            payload = data[len(prefix):]
        elif data.startswith(b'0\r'):
            payload = data[2:]
        else:
            payload = b''

        if len(payload) == 0:
            break

        chunks.append(payload)
        cumulative_bytes += len(payload)
        offset = cumulative_bytes

    if not chunks:
        return None

    compressed = b''.join(chunks)
    try:
        decompressed = gzip.decompress(compressed)
        img = Image.open(io.BytesIO(decompressed)).convert('L')  # to grayscale
        return np.array(img)
    except Exception:
        return None

def main():
    port = find_port()
    print(f"Using port: {port}")

    try:
        ser = serial.Serial(port, 115200, timeout=0.1)
    except Exception as e:
        print(f"Port open failed: {e}", file=sys.stderr)
        sys.exit(1)

    fig, ax = plt.subplots()
    ax.axis('off')
    im = ax.imshow(np.zeros((240, 320)), cmap='gray', vmin=0, vmax=255)
    fig.canvas.manager.set_window_title("Fluke 289 Live")
    fig.tight_layout()

    last_time = time.time()
    frame_count = 0

    def update(frame):
        nonlocal last_time, frame_count
        bitmap = capture_screenshot(ser)
        if bitmap is not None:
            im.set_data(bitmap)
            frame_count += 1
            fps = frame_count / (time.time() - last_time)
            fig.canvas.manager.set_window_title(f"Fluke 289 Live – {fps:.1f} fps")
        return [im]

    # Doug: I decreased the interval to 300ms. It produces about 2 updates per second.
    # ani = FuncAnimation(fig, update, interval=900, blit=True, cache_frame_data=False)
    ani = FuncAnimation(fig, update, interval=300, blit=True, cache_frame_data=False)

    try:
        plt.show()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        ser.close()
        print("Serial port closed")

if __name__ == "__main__":
    main()