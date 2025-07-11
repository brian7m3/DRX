# -*- coding: utf-8 -*-
import os
import time
import threading
import configparser
import serial
import curses
import subprocess
import random
import re

# Constants
SCRIPT_NAME = "DRX"
VERSION = "2.00"

# Globals
serial_buffer = ""
serial_history = []  # Holds last 5 serial commands
currently_playing = ""
currently_playing_info = ""
playing_end_time = 0  # Timestamp until when to display the playing info

# Load config
config = configparser.ConfigParser()
config.read('/home/brian/DRX/config.ini')

SOUND_DIRECTORY = config['Sound']['directory']
SOUND_FILE_EXTENSION = config['Sound']['extension']
SOUND_DEVICE = config['Sound'].get('device', 'default')

# Serial port setup
serial_port = serial.Serial(
    port=config['Serial']['port'],
    baudrate=int(config['Serial']['baudrate']),
    timeout=float(config['Serial']['timeout'])
)

# Data structures for intervals and current play state per base
random_last_played = {}
random_current_track = {}

rotation_last_played = {}
rotation_current_track = {}

sudo_random_last_played = {}
sudo_random_current_track = {}

def play_sound(filename, interruptible=False, loop=False, restartable=False):
    global currently_playing, currently_playing_info, playing_end_time
    currently_playing = os.path.basename(filename)
    currently_playing_info = f"Playing sound on device: {SOUND_DEVICE} - file: {filename} (Interruptible={interruptible}, Loop={loop}, Restartable={restartable})"
    playing_end_time = time.time() + 5  # Display for 5 seconds
    try:
        subprocess.run(
            ['aplay', '-D', SOUND_DEVICE, filename],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"Error playing sound on device '{SOUND_DEVICE}': {e}")
    finally:
        currently_playing = ""
        currently_playing_info = ""
        playing_end_time = 0

def find_matching_files(base, end):
    files = []
    for track_num in range(base, end + 1):
        pattern1 = f"{track_num:04d}-"
        pattern2 = f"{track_num:04d}."
        matching = [f for f in os.listdir(SOUND_DIRECTORY)
                    if (f.startswith(pattern1) or f.startswith(pattern2)) and f.endswith(SOUND_FILE_EXTENSION)]
        files.extend([os.path.join(SOUND_DIRECTORY, f) for f in matching])
    return files

def play_randomized_section(base, end, interval, last_played_dict, current_track_dict):
    current_time = time.time()

    last_played = last_played_dict.get(base, 0)
    current_track = current_track_dict.get(base, None)

    if current_time - last_played >= interval or current_track is None:
        matching_files = find_matching_files(base, end)
        if not matching_files:
            print(f"No matching files found in range {base} to {end}")
            return
        new_track = random.choice(matching_files)
        current_track_dict[base] = new_track
        last_played_dict[base] = current_time
        print(f"[{SCRIPT_NAME} {VERSION}] New random track for base {base}: {os.path.basename(new_track)}")
    else:
        new_track = current_track
        remaining = interval - (current_time - last_played)
        print(f"[{SCRIPT_NAME} {VERSION}] Reusing track for base {base}: {os.path.basename(new_track)} (remaining interval {remaining:.1f}s)")

    threading.Thread(target=play_sound, args=(new_track,)).start()

def play_rotating_section(base, end, interval, last_played_dict, current_track_dict):
    current_time = time.time()

    last_played = last_played_dict.get(base, 0)
    current_track = current_track_dict.get(base, base)

    if current_time - last_played >= interval:
        next_track_num = current_track + 1
        if next_track_num > end:
            next_track_num = base
        base_filename_prefix = f"{next_track_num:04d}"
        matching_files = [f for f in os.listdir(SOUND_DIRECTORY)
                          if (f.startswith(f"{base_filename_prefix}-") or f.startswith(f"{base_filename_prefix}.")) and f.endswith(SOUND_FILE_EXTENSION)]
        if not matching_files:
            print(f"No matching files found for rotating track {next_track_num}")
            return
        next_track = os.path.join(SOUND_DIRECTORY, matching_files[0])
        current_track_dict[base] = next_track_num
        last_played_dict[base] = current_time
        print(f"[{SCRIPT_NAME} {VERSION}] Rotating to track {next_track_num} for base {base}")
    else:
        next_track = None
        remaining = interval - (current_time - last_played)
        current_track_num = current_track_dict.get(base, base)
        base_filename_prefix = f"{current_track_num:04d}"
        matching_files = [f for f in os.listdir(SOUND_DIRECTORY)
                          if (f.startswith(f"{base_filename_prefix}-") or f.startswith(f"{base_filename_prefix}.")) and f.endswith(SOUND_FILE_EXTENSION)]
        if matching_files:
            next_track = os.path.join(SOUND_DIRECTORY, matching_files[0])
            print(f"[{SCRIPT_NAME} {VERSION}] Reusing rotating track {current_track_num} for base {base} (remaining interval {remaining:.1f}s)")
        else:
            print(f"No matching files found for rotating track {current_track_num}")

    if next_track:
        threading.Thread(target=play_sound, args=(next_track,)).start()

def play_sudo_random_section(base, end, interval, last_played_dict, current_track_dict):
    play_randomized_section(base, end, interval, last_played_dict, current_track_dict)

def process_command(command):
    code_str = command[len(config['Direct']['prefix']):]
    if not code_str.isdigit():
        print(f"Invalid code received: {command}")
        return
    code = int(code_str)

    # Update serial history
    serial_history.append(command)
    if len(serial_history) > 5:
        serial_history.pop(0)

    # Check Random
    if 'Random' in config:
        bases = list(map(int, config['Random']['base'].split(',')))
        ends = list(map(int, config['Random']['end'].split(',')))
        intervals = list(map(int, config['Random']['interval'].split(',')))
        for b, e, t in zip(bases, ends, intervals):
            if b <= code <= e:
                play_randomized_section(b, e, t * 60, random_last_played, random_current_track)
                return

    # Check Rotation
    if 'Rotation' in config:
        bases = list(map(int, config['Rotation']['base'].split(',')))
        ends = list(map(int, config['Rotation']['end'].split(',')))
        intervals = list(map(int, config['Rotation']['time'].split(',')))
        for b, e, t in zip(bases, ends, intervals):
            if b <= code <= e:
                play_rotating_section(b, e, t * 60, rotation_last_played, rotation_current_track)
                return

    # Check SudoRandom
    if 'SudoRandom' in config:
        bases = list(map(int, config['SudoRandom']['base'].split(',')))
        ends = list(map(int, config['SudoRandom']['end'].split(',')))
        intervals = list(map(int, config['SudoRandom']['interval'].split(',')))
        for b, e, t in zip(bases, ends, intervals):
            if b <= code <= e:
                play_sudo_random_section(b, e, t * 60, sudo_random_last_played, sudo_random_current_track)
                return

    # Direct play
    if config.getboolean('Direct', 'enabled', fallback=False):
        filename = os.path.join(SOUND_DIRECTORY, f"{code_str}{SOUND_FILE_EXTENSION}")
        if os.path.exists(filename):
            print(f"Playing direct file for code {code_str}: {filename}")
            threading.Thread(target=play_sound, args=(filename,)).start()
        else:
            print(f"Direct play enabled but file not found: {filename}")

def status_screen(stdscr):
    global serial_buffer, currently_playing, currently_playing_info, playing_end_time
    curses.curs_set(0)
    stdscr.nodelay(True)
    while True:
        stdscr.erase()
        stdscr.addstr(0, 0, f"{SCRIPT_NAME} v{VERSION} - Status Screen")
        y = 1

        # Random bases status
        stdscr.addstr(y, 0, "Random Bases State:")
        y += 1
        if 'Random' in config:
            bases = list(map(int, config['Random']['base'].split(',')))
            ends = list(map(int, config['Random']['end'].split(',')))
            intervals = list(map(int, config['Random']['interval'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                last = random_last_played.get(b, 0)
                current = random_current_track.get(b, 'N/A')
                remaining = max(0, t*60 - (time.time() - last))
                track_name = os.path.basename(current) if current != 'N/A' else 'N/A'
                stdscr.addstr(y, 0, f"Base {b}: Track={track_name} Remaining={remaining:.1f}s")
                y += 1

        # Rotation bases status
        stdscr.addstr(y, 0, "Rotation Bases State:")
        y += 1
        if 'Rotation' in config:
            bases = list(map(int, config['Rotation']['base'].split(',')))
            ends = list(map(int, config['Rotation']['end'].split(',')))
            intervals = list(map(int, config['Rotation']['time'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                last = rotation_last_played.get(b, 0)
                current_num = rotation_current_track.get(b, b)
                remaining = max(0, t*60 - (time.time() - last))
                stdscr.addstr(y, 0, f"Base {b}: Track={current_num} Remaining={remaining:.1f}s")
                y += 1

        # SudoRandom bases status
        stdscr.addstr(y, 0, "SudoRandom Bases State:")
        y += 1
        if 'SudoRandom' in config:
            bases = list(map(int, config['SudoRandom']['base'].split(',')))
            ends = list(map(int, config['SudoRandom']['end'].split(',')))
            intervals = list(map(int, config['SudoRandom']['interval'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                last = sudo_random_last_played.get(b, 0)
                current = sudo_random_current_track.get(b, 'N/A')
                remaining = max(0, t*60 - (time.time() - last))
                track_name = os.path.basename(current) if current != 'N/A' else 'N/A'
                stdscr.addstr(y, 0, f"Base {b}: Track={track_name} Remaining={remaining:.1f}s")
                y += 1

        y += 1

        # Serial Buffer line
        stdscr.move(y, 0)
        stdscr.clrtoeol()
        serial_display = ' | '.join(serial_history[-5:]) if serial_history else 'None'
        stdscr.addstr(y, 0, f"Serial Buffer: {serial_display}")
        y += 1

        # Currently Playing line
        stdscr.move(y, 0)
        stdscr.clrtoeol()
        stdscr.addstr(y, 0, f"Currently Playing: {currently_playing if currently_playing else 'None'}")
        y += 1

        # Playing sound on device info (left-justified, visible for 5 seconds)
        stdscr.move(y, 0)
        stdscr.clrtoeol()
        if currently_playing_info and time.time() < playing_end_time:
            stdscr.addstr(y, 0, currently_playing_info)
        y += 1

        stdscr.refresh()

        try:
            if stdscr.getkey() == 'q':
                break
        except curses.error:
            pass

        time.sleep(0.5)

def serial_read_loop():
    global serial_buffer
    while True:
        if serial_port.in_waiting:
            data = serial_port.read(serial_port.in_waiting)
            try:
                decoded = data.decode('ascii')
                serial_buffer += decoded
            except UnicodeDecodeError:
                pass
        time.sleep(0.05)

def process_serial_commands():
    global serial_buffer
    prefix = config['Direct']['prefix']
    pattern = rf"{prefix}\d{{4}}"
    while True:
        match = re.search(pattern, serial_buffer)
        if match:
            command = match.group(0)
            process_command(command)
            serial_buffer = serial_buffer.replace(command, '', 1)
        time.sleep(0.1)

def main():
    threading.Thread(target=serial_read_loop, daemon=True).start()
    threading.Thread(target=process_serial_commands, daemon=True).start()
    curses.wrapper(status_screen)

if __name__ == "__main__":
    main()
