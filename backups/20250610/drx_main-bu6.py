# DRX main process: handles all DRX logic and curses UI.
# Communicates with web dashboard via /tmp/drx_state.json and /tmp/drx_webcmd.json

import os
import sys
import time
import threading
import configparser
import serial
import curses
import subprocess
import random
import re
import RPi.GPIO as GPIO
import datetime
import traceback
import wave
import contextlib
import shutil
import itertools
import json
import string

SCRIPT_NAME = "DRX"
VERSION = "2.01"

STATE_FILE = '/tmp/drx_state.json'
WEBCMD_FILE = '/tmp/drx_webcmd.json'

serial_buffer = ""
serial_history = []
currently_playing = ""
currently_playing_info = ""
currently_playing_info_timestamp = 0
playing_end_time = 0
playback_status = ""

serial_port_missing = False
sound_card_missing = False

script_dir = os.path.dirname(os.path.realpath(__file__))
config_file_path = os.path.join(script_dir, 'config.ini')
log_file_path = os.path.join(script_dir, 'drx_error.log')

config = configparser.ConfigParser()
config.read(config_file_path)
SOUND_DIRECTORY = config['Sound']['directory']
SOUND_FILE_EXTENSION = config['Sound']['extension']
SOUND_DEVICE = config['Sound'].get('device', 'default')

COS_PIN = int(config['GPIO']['cos_pin'])
COS_ACTIVE_LEVEL = config.getboolean('GPIO', 'cos_activate_level')
REMOTE_BUSY_PIN = int(config['GPIO']['remote_busy_pin'])
REMOTE_BUSY_ACTIVE_LEVEL = config.getboolean('GPIO', 'remote_busy_activate_level')
COS_DEBOUNCE_TIME = float(config['GPIO']['cos_debounce_time'])
MAX_COS_INTERRUPTIONS = int(config['GPIO']['max_cos_interruptions'])

GPIO.setmode(GPIO.BCM)
GPIO.setup(REMOTE_BUSY_PIN, GPIO.OUT)
GPIO.output(REMOTE_BUSY_PIN, not REMOTE_BUSY_ACTIVE_LEVEL)
GPIO.setup(COS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

try:
    serial_port = serial.Serial(
        port=config['Serial']['port'],
        baudrate=int(config['Serial']['baudrate']),
        timeout=float(config['Serial']['timeout'])
    )
except Exception as e:
    serial_port = None
    serial_port_missing = True

random_last_played = {}
random_current_track = {}
rotation_last_played = {}
rotation_current_track = {}

sudo_random_last_interval = {}
sudo_random_interval_track = {}
sudo_random_played_in_cycle = {}
sudo_random_last_file = {}

playback_interrupt = threading.Event()

def log_error(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file_path, 'a') as f:
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        print(f"[{timestamp}] Logging failed: {msg}")

def log_exception(context: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exc = traceback.format_exc()
    try:
        with open(log_file_path, 'a') as f:
            f.write(f"[{timestamp}] Exception in {context}:\n{exc}\n")
    except Exception:
        print(f"[{timestamp}] Logging failed: {exc}")

if shutil.which("sox") is None:
    log_error("sox is not installed! 'P' mode will not work.")

def is_cos_active():
    override_enabled = config.getboolean('Debug', 'enable_cos_override', fallback=False)
    override_path = "/tmp/cos_force"
    if override_enabled:
        try:
            with open(override_path, "r") as f:
                val = f.read().strip()
                if val == "1":
                    return True
                elif val == "0":
                    return False
        except FileNotFoundError:
            pass
        except Exception:
            log_exception("is_cos_active (override)")
    try:
        level = GPIO.input(COS_PIN)
        return (level == COS_ACTIVE_LEVEL)
    except Exception:
        log_exception("is_cos_active (GPIO.input)")
        return False

def parse_serial_command(command):
    try:
        m = re.match(r'^P(\d{4})([IRP]?)$', command)
        if not m:
            return None, None
        return m.group(1), m.group(2)
    except Exception:
        log_exception("parse_serial_command")
        return None, None

def find_matching_files(base, end):
    """For group logic, tracks are base+1 to end inclusive."""
    files = []
    try:
        for track_num in range(base + 1, end + 1):
            pattern1 = f"{track_num:04d}-"
            pattern2 = f"{track_num:04d}."
            matching = [f for f in os.listdir(SOUND_DIRECTORY)
                        if (f.startswith(pattern1) or f.startswith(pattern2) or f.startswith(f"{track_num:04d}")) and f.endswith(SOUND_FILE_EXTENSION)]
            files.extend([os.path.join(SOUND_DIRECTORY, f) for f in matching])
    except Exception:
        log_exception("find_matching_files")
    return files

def get_all_sound_files():
    try:
        return sorted([f for f in os.listdir(SOUND_DIRECTORY) if f.endswith(SOUND_FILE_EXTENSION)])
    except Exception:
        return []

def get_duration_wav(fname):
    try:
        with contextlib.closing(wave.open(fname, 'r')) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            duration = frames / float(rate)
            return duration
    except Exception:
        log_exception("get_duration_wav")
        return 0

def play_sound(filename, interruptible=False, repeat=False, pausing=False):
    global currently_playing, currently_playing_info, currently_playing_info_timestamp, playing_end_time, playback_interrupt, playback_status, sound_card_missing
    max_interrupts = MAX_COS_INTERRUPTIONS
    cos_interrupts = 0
    playback_interrupt.clear()
    currently_playing = os.path.basename(filename)
    currently_playing_info = f"Playing sound on device: {SOUND_DEVICE} - file: {filename} (Interruptible={interruptible}, Repeat={repeat}, Pausing={pausing})"
    currently_playing_info_timestamp = time.time()
    playing_end_time = currently_playing_info_timestamp + 5
    try:
        GPIO.output(REMOTE_BUSY_PIN, REMOTE_BUSY_ACTIVE_LEVEL)
        if repeat:
            while True:
                interrupted = False
                allow_interrupt = cos_interrupts < max_interrupts
                playback_status = "Playing"
                try:
                    proc = subprocess.Popen(
                        ['aplay', '-D', SOUND_DEVICE, filename],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                except FileNotFoundError:
                    sound_card_missing = True
                    currently_playing_info = "Sound card/device missing or aplay not found!"
                    return
                except Exception as exc:
                    sound_card_missing = True
                    currently_playing_info = f"Sound card/device error: {exc}"
                    return
                try:
                    while proc.poll() is None:
                        if allow_interrupt and is_cos_active():
                            playback_status = "Restarting"
                            proc.terminate()
                            time.sleep(0.2)
                            if proc.poll() is None:
                                proc.kill()
                            cos_interrupts += 1
                            interrupted = True
                            while is_cos_active() and not playback_interrupt.is_set():
                                time.sleep(0.05)
                            break
                        if playback_interrupt.is_set():
                            proc.terminate()
                            break
                        time.sleep(0.05)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                if interrupted and cos_interrupts < max_interrupts and not playback_interrupt.is_set():
                    continue
                if cos_interrupts >= max_interrupts and not playback_interrupt.is_set():
                    playback_status = "Playing"
                    try:
                        proc = subprocess.Popen(
                            ['aplay', '-D', SOUND_DEVICE, filename],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                    except FileNotFoundError:
                        sound_card_missing = True
                        currently_playing_info = "Sound card/device missing or aplay not found!"
                        return
                    except Exception as exc:
                        sound_card_missing = True
                        currently_playing_info = f"Sound card/device error: {exc}"
                        return
                    try:
                        while proc.poll() is None:
                            if playback_interrupt.is_set():
                                proc.terminate()
                                break
                            time.sleep(0.05)
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait()
                    break
                break
        elif pausing:
            total_duration = get_duration_wav(filename)
            played_duration = 0
            while played_duration < total_duration:
                sox_cmd = [
                    'sox', filename, '-t', 'wav', '-', 'trim', f'{played_duration}'
                ]
                try:
                    proc1 = subprocess.Popen(sox_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    proc2 = subprocess.Popen(['aplay', '-D', SOUND_DEVICE], stdin=proc1.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    proc1.stdout.close()
                except FileNotFoundError:
                    sound_card_missing = True
                    currently_playing_info = "Sound card/device missing or aplay/sox not found!"
                    return
                except Exception as exc:
                    sound_card_missing = True
                    currently_playing_info = f"Sound card/device error: {exc}"
                    return
                interrupted = False
                start_time = time.time()
                playback_status = "Playing"
                while proc2.poll() is None:
                    if is_cos_active():
                        if cos_interrupts < max_interrupts:
                            playback_status = "Pausing"
                            proc2.terminate()
                            proc1.terminate()
                            time.sleep(0.1)
                            if proc2.poll() is None:
                                proc2.kill()
                            if proc1.poll() is None:
                                proc1.kill()
                            cos_interrupts += 1
                            interrupted = True
                            played_duration += time.time() - start_time
                            while is_cos_active() and not playback_interrupt.is_set():
                                time.sleep(0.05)
                            break
                        else:
                            proc2.terminate()
                            proc1.terminate()
                            if proc2.poll() is None:
                                proc2.kill()
                            if proc1.poll() is None:
                                proc1.kill()
                            played_duration = total_duration
                            return
                    if playback_interrupt.is_set():
                        proc2.terminate()
                        proc1.terminate()
                        break
                    time.sleep(0.05)
                if proc2.poll() is None:
                    proc2.kill()
                if proc1.poll() is None:
                    proc1.kill()
                if not interrupted or cos_interrupts >= max_interrupts or playback_interrupt.is_set():
                    break
        else:
            playback_status = "Playing"
            try:
                proc = subprocess.Popen(
                    ['aplay', '-D', SOUND_DEVICE, filename],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                sound_card_missing = True
                currently_playing_info = "Sound card/device missing or aplay not found!"
                return
            except Exception as exc:
                sound_card_missing = True
                currently_playing_info = f"Sound card/device error: {exc}"
                return
            try:
                while proc.poll() is None:
                    if interruptible and is_cos_active():
                        proc.terminate()
                        time.sleep(0.2)
                        if proc.poll() is None:
                            proc.kill()
                        break
                    if playback_interrupt.is_set():
                        proc.terminate()
                        break
                    time.sleep(0.05)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
    except Exception:
        log_exception("play_sound")
    finally:
        playback_status = ""
        try:
            GPIO.output(REMOTE_BUSY_PIN, not REMOTE_BUSY_ACTIVE_LEVEL)
        except Exception:
            log_exception("play_sound (GPIO cleanup)")
        currently_playing = ""
        currently_playing_info = ""
        currently_playing_info_timestamp = 0
        playing_end_time = 0

def play_randomized_section(base, end, interval, last_played_dict, current_track_dict, interruptible=False):
    try:
        current_time = time.time()
        last_played = last_played_dict.get(base, 0)
        current_track = current_track_dict.get(base, None)
        matching_files = find_matching_files(base, end)
        if not matching_files:
            return
        if current_time - last_played >= interval or current_track not in matching_files:
            new_track = random.choice(matching_files)
            current_track_dict[base] = new_track
            last_played_dict[base] = current_time
        else:
            new_track = current_track
        threading.Thread(target=play_sound, args=(new_track, interruptible)).start()
    except Exception:
        log_exception("play_randomized_section")

def play_rotating_section(base, end, interval, last_played_dict, current_track_dict, interruptible=False):
    try:
        current_time = time.time()
        last_played = last_played_dict.get(base, 0)
        available_tracks = []
        for track_num in range(base + 1, end + 1):
            base_filename_prefix = f"{track_num:04d}"
            matching_files = [f for f in os.listdir(SOUND_DIRECTORY)
                              if (f.startswith(f"{base_filename_prefix}-") or f.startswith(f"{base_filename_prefix}.") or f.startswith(base_filename_prefix)) and f.endswith(SOUND_FILE_EXTENSION)]
            if matching_files:
                available_tracks.append(track_num)
        if not available_tracks:
            return
        if base not in current_track_dict or current_track_dict[base] not in available_tracks:
            current_track_num = available_tracks[0]
        else:
            current_track_num = current_track_dict[base]
        if last_played == 0:
            next_track_num = current_track_num
        elif current_time - last_played >= interval:
            idx = available_tracks.index(current_track_num) if current_track_num in available_tracks else 0
            next_idx = (idx + 1) % len(available_tracks)
            next_track_num = available_tracks[next_idx]
        else:
            next_track_num = current_track_num
        base_filename_prefix = f"{next_track_num:04d}"
        matching_files = [f for f in os.listdir(SOUND_DIRECTORY)
                          if (f.startswith(f"{base_filename_prefix}-") or f.startswith(f"{base_filename_prefix}.") or f.startswith(base_filename_prefix)) and f.endswith(SOUND_FILE_EXTENSION)]
        if not matching_files:
            return
        next_track = os.path.join(SOUND_DIRECTORY, matching_files[0])
        current_track_dict[base] = next_track_num
        last_played_dict[base] = current_time
        threading.Thread(target=play_sound, args=(next_track, interruptible)).start()
    except Exception:
        log_exception("play_rotating_section")

def play_sudo_random_section(base, end, interval, last_interval_dict, interval_track_dict, played_in_cycle_dict, interruptible=False, repeat=False, pausing=False):
    global sudo_random_last_file
    current_time = time.time()
    matching_files = find_matching_files(base, end)
    if not matching_files:
        return
    last_interval = last_interval_dict.get(base, 0)
    current_track = interval_track_dict.get(base)
    played_in_cycle = played_in_cycle_dict.get(base, set())
    if current_track is not None and (current_time - last_interval < interval) and current_track in matching_files:
        file_to_play = current_track
    else:
        unused_tracks = [t for t in matching_files if t not in played_in_cycle]
        if not unused_tracks:
            played_in_cycle = set()
            unused_tracks = matching_files[:]
        new_track = random.choice(unused_tracks)
        file_to_play = new_track
        interval_track_dict[base] = file_to_play
        last_interval_dict[base] = current_time
        played_in_cycle.add(file_to_play)
        played_in_cycle_dict[base] = played_in_cycle
    sudo_random_last_file[base] = file_to_play
    threading.Thread(target=play_sound, args=(file_to_play, interruptible, repeat, pausing)).start()

def play_direct_track(code_str, interruptible=False, repeat=False, pausing=False):
    filename = os.path.join(SOUND_DIRECTORY, f"{code_str}{SOUND_FILE_EXTENSION}")
    if os.path.exists(filename):
        threading.Thread(target=play_sound, args=(filename, interruptible, repeat, pausing)).start()

def process_command(command):
    global playback_interrupt
    try:
        playback_interrupt.set()
        time.sleep(0.1)
        code_str, suffix = parse_serial_command(command.strip())
        if code_str is None:
            return
        code = int(code_str)
        interruptible = (suffix == "I")
        repeat = (suffix == "R")
        pausing = (suffix == "P")
        if 'Random' in config:
            bases = list(map(int, config['Random']['base'].split(',')))
            ends = list(map(int, config['Random']['end'].split(',')))
            intervals = list(map(int, config['Random']['interval'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                if code == b:
                    play_randomized_section(b, e, t * 60, random_last_played, random_current_track, interruptible)
                    return
                elif b < code <= e:
                    play_direct_track(code_str, interruptible, repeat, pausing)
                    return
        if 'Rotation' in config:
            bases = list(map(int, config['Rotation']['base'].split(',')))
            ends = list(map(int, config['Rotation']['end'].split(',')))
            intervals = list(map(int, config['Rotation']['time'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                if code == b:
                    play_rotating_section(b, e, t * 60, rotation_last_played, rotation_current_track, interruptible)
                    return
                elif b < code <= e:
                    play_direct_track(code_str, interruptible, repeat, pausing)
                    return
        if 'SudoRandom' in config:
            bases = list(map(int, config['SudoRandom']['base'].split(',')))
            ends = list(map(int, config['SudoRandom']['end'].split(',')))
            intervals = list(map(int, config['SudoRandom']['interval'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                if code == b:
                    play_sudo_random_section(
                        b, e, t * 60,
                        sudo_random_last_interval,
                        sudo_random_interval_track,
                        sudo_random_played_in_cycle,
                        interruptible, repeat, pausing
                    )
                    return
                elif b < code <= e:
                    play_direct_track(code_str, interruptible, repeat, pausing)
                    return
        if config.getboolean('Direct', 'enabled', fallback=False):
            play_direct_track(code_str, interruptible, repeat, pausing)
    except Exception:
        log_exception("process_command")

def write_state():
    state = {
        'currently_playing': currently_playing,
        'playback_status': playback_status,
        'serial_history': serial_history[-10:],
        'sound_card_missing': sound_card_missing,
        'serial_port_missing': serial_port_missing,
        'random_last_played': random_last_played,
        'random_current_track': {k: os.path.basename(v) if v else v for k, v in random_current_track.items()},
        'rotation_last_played': rotation_last_played,
        'rotation_current_track': rotation_current_track,
        'sudo_random_last_interval': sudo_random_last_interval,
        'sudo_random_interval_track': {k: os.path.basename(v) if v else v for k, v in sudo_random_interval_track.items()},
        'sudo_random_played_in_cycle': {k: [os.path.basename(x) for x in v] for k, v in sudo_random_played_in_cycle.items()},
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception:
        pass

def reload_config():
    global config, SOUND_DIRECTORY, SOUND_FILE_EXTENSION, SOUND_DEVICE
    global COS_PIN, COS_ACTIVE_LEVEL, REMOTE_BUSY_PIN, REMOTE_BUSY_ACTIVE_LEVEL
    global COS_DEBOUNCE_TIME, MAX_COS_INTERRUPTIONS
    try:
        config.read(config_file_path)
        SOUND_DIRECTORY = config['Sound']['directory']
        SOUND_FILE_EXTENSION = config['Sound']['extension']
        SOUND_DEVICE = config['Sound'].get('device', 'default')
        COS_PIN = int(config['GPIO']['cos_pin'])
        COS_ACTIVE_LEVEL = config.getboolean('GPIO', 'cos_activate_level')
        REMOTE_BUSY_PIN = int(config['GPIO']['remote_busy_pin'])
        REMOTE_BUSY_ACTIVE_LEVEL = config.getboolean('GPIO', 'remote_busy_activate_level')
        COS_DEBOUNCE_TIME = float(config['GPIO']['cos_debounce_time'])
        MAX_COS_INTERRUPTIONS = int(config['GPIO']['max_cos_interruptions'])
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(REMOTE_BUSY_PIN, GPIO.OUT)
            GPIO.output(REMOTE_BUSY_PIN, not REMOTE_BUSY_ACTIVE_LEVEL)
            GPIO.setup(COS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        except Exception:
            log_exception("reload_config (GPIO setup)")
    except Exception:
        log_exception("reload_config")

def maybe_run_webcmd():
    if os.path.exists(WEBCMD_FILE):
        try:
            with open(WEBCMD_FILE, 'r') as f:
                cmd = json.load(f)
            if cmd.get("type") == "play":
                input_cmd = cmd.get("input", "")
                if input_cmd.lower().endswith('.wav'):
                    path = os.path.join(SOUND_DIRECTORY, input_cmd)
                    if os.path.exists(path):
                        threading.Thread(target=play_sound, args=(path,), daemon=True).start()
                else:
                    process_command(input_cmd)
            elif cmd.get("type") == "stop":
                playback_interrupt.set()
            elif cmd.get("type") == "reload_config":
                reload_config()
            os.remove(WEBCMD_FILE)
        except Exception:
            log_exception("maybe_run_webcmd")

def serial_read_loop():
    global serial_buffer, serial_port_missing
    while True:
        try:
            if serial_port and serial_port.in_waiting:
                data = serial_port.read(serial_port.in_waiting)
                try:
                    decoded = data.decode('ascii')
                    cleaned = ''.join(c for c in decoded if c in string.printable and c not in '\x1b')
                    serial_buffer += cleaned
                    for line in cleaned.splitlines():
                        if line.strip():
                            serial_history.insert(0, line.strip())
                            if len(serial_history) > 10:
                                serial_history.pop()
                except UnicodeDecodeError:
                    log_error("UnicodeDecodeError in serial_read_loop")
        except Exception:
            serial_port_missing = True
            log_exception("serial_read_loop")
        time.sleep(0.05)

def process_serial_commands():
    global serial_buffer
    prefix = config['Direct']['prefix']
    pattern = rf"{prefix}\d{{4}}[IRP]?"
    while True:
        try:
            match = re.search(pattern, serial_buffer)
            if match:
                command = match.group(0)
                process_command(command)
                serial_buffer = serial_buffer.replace(command, '', 1)
        except Exception:
            log_exception("process_serial_commands")
        time.sleep(0.1)

def status_screen(stdscr):
    global serial_buffer, currently_playing, currently_playing_info, currently_playing_info_timestamp, playing_end_time, playback_status, serial_port_missing, sound_card_missing
    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(6, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.curs_set(0)
    stdscr.nodelay(True)
    flash_state = itertools.cycle([True, False])
    while True:
        try:
            stdscr.erase()
            y = 0
            warning_msgs = []
            if serial_port_missing:
                warning_msgs.append("SERIAL PORT NOT FOUND")
            if sound_card_missing:
                warning_msgs.append("SOUND CARD/DEVICE NOT FOUND")
            if warning_msgs and next(flash_state):
                stdscr.addstr(y, 0, " | ".join(warning_msgs), curses.color_pair(6) | curses.A_BOLD)
                y += 1
            stdscr.addstr(y, 0, f"{SCRIPT_NAME} v{VERSION} - Status Screen", curses.color_pair(1))
            y += 1
            y += 1
            stdscr.addstr(y, 0, "Random Bases State:", curses.color_pair(2))
            y += 1
            if 'Random' in config:
                bases = list(map(int, config['Random']['base'].split(',')))
                ends = list(map(int, config['Random']['end'].split(',')))
                intervals = list(map(int, config['Random']['interval'].split(',')))
                for b, e, t in zip(bases, ends, intervals):
                    last = random_last_played.get(b, 0)
                    current = random_current_track.get(b, 'N/A')
                    remaining = max(0, t*60 - (time.time() - last))
                    track_name = os.path.basename(current) if current != 'N/A' and current else 'N/A'
                    stdscr.addstr(y, 0, f"Base {b} | End {e} Interval {t}: Track={track_name} Remaining={remaining:.1f}s", curses.color_pair(3))
                    y += 1
            y += 1
            stdscr.addstr(y, 0, "Rotation Bases State:", curses.color_pair(2))
            y += 1
            if 'Rotation' in config:
                bases = list(map(int, config['Rotation']['base'].split(',')))
                ends = list(map(int, config['Rotation']['end'].split(',')))
                intervals = list(map(int, config['Rotation']['time'].split(',')))
                for b, e, t in zip(bases, ends, intervals):
                    last = rotation_last_played.get(b, 0)
                    current_num = rotation_current_track.get(b, b+1)
                    remaining = max(0, t*60 - (time.time() - last))
                    stdscr.addstr(y, 0, f"Base {b} | End {e} Interval {t}: Track={current_num} Remaining={remaining:.1f}s", curses.color_pair(3))
                    y += 1
            y += 1
            stdscr.addstr(y, 0, "SudoRandom Bases State:", curses.color_pair(2))
            y += 1
            if 'SudoRandom' in config:
                bases = list(map(int, config['SudoRandom']['base'].split(',')))
                ends = list(map(int, config['SudoRandom']['end'].split(',')))
                intervals = list(map(int, config['SudoRandom']['interval'].split(',')))
                for b, e, t in zip(bases, ends, intervals):
                    last = sudo_random_last_interval.get(b, 0)
                    current = sudo_random_interval_track.get(b, 'N/A')
                    played = sudo_random_played_in_cycle.get(b, set())
                    remaining = max(0, t*60 - (time.time() - last))
                    track_name = os.path.basename(current) if current != 'N/A' and current else 'N/A'
                    stdscr.addstr(y, 0, f"Base {b} | End {e} Interval {t}: Track={track_name} Remaining={remaining:.1f}s PlayedInCycle={len(played)}", curses.color_pair(3))
                    y += 1
            y += 1
            stdscr.move(y, 0)
            stdscr.clrtoeol()
            serial_display = ' | '.join(
                ''.join(c for c in s if c in string.printable and c not in '\x1b') for s in serial_history[-5:]
            ) if serial_history else 'None'
            stdscr.addstr(y, 0, f"Serial Buffer: {serial_display}", curses.color_pair(5))
            y += 1
            y += 1
            stdscr.move(y, 0)
            stdscr.clrtoeol()
            if playback_status == "Pausing":
                label = "Currently Pausing:"
            elif playback_status == "Restarting":
                label = "Currently Restarting:"
            else:
                label = "Currently Playing:"
            info_clean = ''.join(c for c in currently_playing if c in string.printable and c not in '\x1b')
            stdscr.addstr(y, 0, f"{label} {info_clean if info_clean else 'None'}", curses.color_pair(4))
            y += 1
            if currently_playing_info:
                if time.time() - currently_playing_info_timestamp < 5:
                    info2 = ''.join(c for c in currently_playing_info if c in string.printable and c not in '\x1b')
                    stdscr.addstr(y, 0, info2, curses.color_pair(4))
                else:
                    currently_playing_info = ""
                    currently_playing_info_timestamp = 0
            y += 1
            y += 1
            cos_state = is_cos_active()
            cos_color = curses.color_pair(2) if cos_state else curses.color_pair(5)
            stdscr.addstr(y, 0, f"COS Active: {'YES' if cos_state else 'NO'}", cos_color)
            y += 1
            y += 1
            stdscr.move(y, 0)
            stdscr.clrtoeol()
            max_y, _ = stdscr.getmaxyx()
            stdscr.move(max_y - 1, 0)
            stdscr.clrtoeol()
            stdscr.addstr(max_y - 1, 0, "Press q to quit", curses.color_pair(1))
            stdscr.refresh()
            try:
                if stdscr.getkey() == 'q':
                    break
            except curses.error:
                pass
            time.sleep(0.5)
        except Exception:
            log_exception("status_screen")

def main():
    try:
        threading.Thread(target=serial_read_loop, daemon=True).start()
        threading.Thread(target=process_serial_commands, daemon=True).start()
        threading.Thread(target=bg_write_state_and_webcmd_loop, daemon=True).start()
        curses.wrapper(status_screen)
    except KeyboardInterrupt:
        pass
    except Exception:
        log_exception("main")
    finally:
        try:
            GPIO.cleanup()
        except Exception:
            log_exception("main (GPIO cleanup)")

def bg_write_state_and_webcmd_loop():
    while True:
        maybe_run_webcmd()
        write_state()
        time.sleep(0.25)

if __name__ == "__main__":
    main()