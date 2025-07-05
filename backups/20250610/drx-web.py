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
import RPi.GPIO as GPIO
import datetime
import traceback
import wave
import contextlib
import shutil
import itertools
import sys

import logging
from flask import Flask, render_template_string, redirect, url_for, request, session

logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('flask').setLevel(logging.ERROR)
sys.stdout = open(os.devnull, 'w')
sys.stderr = open(os.devnull, 'w')

FLASK_SECRET_KEY = "change_this_to_a_random_secret"

SCRIPT_NAME = "DRX"
VERSION = "2.06"

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

def get_web_credentials():
    config.read(config_file_path)
    webuser = config['WebAuth'].get('username', 'admin')
    webpass = config['WebAuth'].get('password', 'drxpass')
    return webuser, webpass

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
    files = []
    try:
        for track_num in range(base, end + 1):
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

def play_specific_track(trackname):
    path = os.path.join(SOUND_DIRECTORY, trackname)
    if os.path.exists(path):
        threading.Thread(target=play_sound, args=(path,), daemon=True).start()
        return True
    return False

def reload_config():
    global config, SOUND_DIRECTORY, SOUND_FILE_EXTENSION, SOUND_DEVICE, COS_PIN, COS_ACTIVE_LEVEL, REMOTE_BUSY_PIN, REMOTE_BUSY_ACTIVE_LEVEL, COS_DEBOUNCE_TIME, MAX_COS_INTERRUPTIONS
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

def restart_script():
    os.execv(sys.executable, [sys.executable] + sys.argv)

def reboot_system():
    subprocess.Popen(['sudo', 'reboot'])

def save_config_file(new_content):
    with open(config_file_path, 'w') as f:
        f.write(new_content)

def get_config_file_content():
    with open(config_file_path, 'r') as f:
        return f.read()

def process_command(command):
    global playback_interrupt
    try:
        playback_interrupt.set()
        time.sleep(0.1)
        code_str, suffix = parse_serial_command(command.strip())
        if code_str is None:
            return
        filename = os.path.join(SOUND_DIRECTORY, f"{code_str}{SOUND_FILE_EXTENSION}")
        interruptible = (suffix == "I")
        repeat = (suffix == "R")
        pausing = (suffix == "P")
        if 'Random' in config:
            bases = list(map(int, config['Random']['base'].split(',')))
            ends = list(map(int, config['Random']['end'].split(',')))
            intervals = list(map(int, config['Random']['interval'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                if b <= int(code_str) <= e:
                    play_randomized_section(b, e, t * 60, random_last_played, random_current_track, interruptible)
                    return
        if 'Rotation' in config:
            bases = list(map(int, config['Rotation']['base'].split(',')))
            ends = list(map(int, config['Rotation']['end'].split(',')))
            intervals = list(map(int, config['Rotation']['time'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                if b <= int(code_str) <= e:
                    play_rotating_section(b, e, t * 60, rotation_last_played, rotation_current_track, interruptible)
                    return
        if 'SudoRandom' in config:
            bases = list(map(int, config['SudoRandom']['base'].split(',')))
            ends = list(map(int, config['SudoRandom']['end'].split(',')))
            intervals = list(map(int, config['SudoRandom']['interval'].split(',')))
            for b, e, t in zip(bases, ends, intervals):
                if b <= int(code_str) <= e:
                    play_sudo_random_section(
                        b, e, t * 60,
                        sudo_random_last_interval,
                        sudo_random_interval_track,
                        sudo_random_played_in_cycle,
                        interruptible, repeat, pausing
                    )
                    return
        if config.getboolean('Direct', 'enabled', fallback=False):
            if os.path.exists(filename):
                threading.Thread(target=play_sound, args=(filename, interruptible, repeat, pausing)).start()
    except Exception:
        log_exception("process_command")

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
                    track_name = os.path.basename(current) if current != 'N/A' else 'N/A'
                    stdscr.addstr(y, 0, f"Base {b} | End {e}: Track={track_name} Remaining={remaining:.1f}s", curses.color_pair(3))
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
                    current_num = rotation_current_track.get(b, b)
                    remaining = max(0, t*60 - (time.time() - last))
                    stdscr.addstr(y, 0, f"Base {b} | End {e}: Track={current_num} Remaining={remaining:.1f}s", curses.color_pair(3))
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
                    track_name = os.path.basename(current) if current != 'N/A' else 'N/A'
                    stdscr.addstr(y, 0, f"Base {b} | End {e}: Track={track_name} Remaining={remaining:.1f}s PlayedInCycle={len(played)}", curses.color_pair(3))
                    y += 1
            y += 1
            stdscr.move(y, 0)
            stdscr.clrtoeol()
            serial_display = ' | '.join(serial_history[-5:]) if serial_history else 'None'
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
            stdscr.addstr(y, 0, f"{label} {currently_playing if currently_playing else 'None'}", curses.color_pair(4))
            y += 1
            if currently_playing_info:
                if time.time() - currently_playing_info_timestamp < 5:
                    stdscr.addstr(y, 0, currently_playing_info, curses.color_pair(4))
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

def serial_read_loop():
    global serial_buffer, serial_port_missing
    while True:
        try:
            if serial_port and serial_port.in_waiting:
                data = serial_port.read(serial_port.in_waiting)
                try:
                    decoded = data.decode('ascii')
                    serial_buffer += decoded
                    for line in decoded.splitlines():
                        if line.strip():
                            serial_history.insert(0, line.strip())
                            if len(serial_history) > 5:
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

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
web_log = []

DASHBOARD_TEMPLATE = '''
<!doctype html>
<html>
<head>
<title>DRX Dashboard</title>
{% if session.get("auto_refresh", True) %}
<meta http-equiv="refresh" content="1">
{% endif %}
<link href="https://fonts.googleapis.com/css?family=Roboto:400,700&display=swap" rel="stylesheet">
<style>
body {
    font-family: 'Roboto', Arial, sans-serif;
    background: #f5f7fa;
    margin: 0;
    padding: 0;
}
#main-card {
    background: #fff;
    max-width: 900px;
    margin: 40px auto 40px auto;
    border-radius: 16px;
    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1);
    padding: 2em 2.5em 2em 2.5em;
}
h1 {
    margin-top: 0;
    color: #3949ab;
    font-weight: 700;
    letter-spacing: 1px;
}
h2 {
    color: #3949ab;
    border-bottom: 1px solid #e3e6f0;
    padding-bottom: 0.3em;
}
ul {
    padding-left: 1.2em;
}
li {
    margin-bottom: 0.3em;
}
form {
    margin-bottom: 1.5em;
}
input[type=text], select, textarea {
    font-size: 1.1em;
    border: 1px solid #bdbdbd;
    border-radius: 5px;
    padding: 0.4em;
    margin-right: 0.5em;
    background: #f9f9fc;
    color: #2d2d2d;
}
textarea {
    font-family: 'Roboto Mono', monospace;
    width: 100%;
}
button {
    font-size: 1.1em;
    border: none;
    border-radius: 5px;
    background: linear-gradient(90deg,#3949ab,#1976d2);
    color: #fff;
    padding: 0.5em 1.2em;
    cursor: pointer;
    transition: background 0.2s;
    margin-top: 0.5em;
    margin-bottom: 0.5em;
}
button:hover {
    background: linear-gradient(90deg,#1976d2,#3949ab);
}
#logout-btn {
    float: right;
    margin-top: 10px;
}
.status-list li {
    font-size: 1.1em;
    margin-bottom: 0.7em;
}
.status-good { color: #388e3c; font-weight: bold; }
.status-warn { color: #fbc02d; font-weight: bold; }
.status-bad { color: #d32f2f; font-weight: bold; }
.card-section {
    background: #f1f3fa;
    border-radius: 10px;
    padding: 1em 1.5em;
    margin-bottom: 2em;
    box-shadow: 0 2px 8px 0 rgba(31, 38, 135, 0.07);
}
.card-section ul, .card-section ol { margin: 0; }
.logs, .serials {
    background: #212121;
    color: #ececec;
    font-family: 'Roboto Mono', monospace;
    font-size: 1em;
    padding: 1em;
    border-radius: 7px;
    margin-top: 0.7em;
    margin-bottom: 1.2em;
    overflow-x: auto;
    max-height: 200px;
}
.label {
    background: #3949ab;
    color: #fff;
    border-radius: 4px;
    padding: 0.1em 0.5em;
    font-size: 0.95em;
    margin-right: 0.4em;
}
@media (max-width: 650px) {
    #main-card { padding: 1em 0.5em;}
    h1 { font-size: 1.3em;}
    h2 { font-size: 1.07em;}
    .card-section { padding: 0.6em 0.4em;}
    button { width: 100%; }
}
</style>
</head>
<body>
<div id="main-card">
    <form method="POST" action="{{ url_for('logout') }}" id="logout-btn">
        <button type="submit">Logout</button>
    </form>
    <h1>DRX Status Dashboard</h1>
    <form method="POST" action="{{ url_for('toggle_refresh') }}">
        <button type="submit">
            {% if session.get("auto_refresh", True) %}
            Turn Auto-Refresh OFF
            {% else %}
            Turn Auto-Refresh ON
            {% endif %}
        </button>
        <span class="label">Auto-Refresh: {{ "ON" if session.get("auto_refresh", True) else "OFF" }}</span>
    </form>
    <div class="card-section">
        <ul class="status-list">
            <li><b>Currently Playing:</b> <span class="status-good">{{ currently_playing or "None" }}</span></li>
            <li><b>Status:</b> <span>{{ playback_status or "Idle" }}</span></li>
            <li><b>COS Active:</b>
                {% if cos_state == "YES" %}
                    <span class="status-good">YES</span>
                {% else %}
                    <span class="status-warn">NO</span>
                {% endif %}
            </li>
            <li><b>Serial Port:</b>
                {% if not serial_port_missing %}
                    <span class="status-good">OK</span>
                {% else %}
                    <span class="status-bad">Missing</span>
                {% endif %}
            </li>
            <li><b>Sound Card:</b>
                {% if not sound_card_missing %}
                    <span class="status-good">OK</span>
                {% else %}
                    <span class="status-bad">Missing</span>
                {% endif %}
            </li>
        </ul>
    </div>
    <div class="card-section">
        <form method="POST" action="{{ url_for('stop_playback') }}">
            <button type="submit">Stop Playback</button>
        </form>
        <form method="POST" action="{{ url_for('restart_script_web') }}">
            <button type="submit" onclick="return confirm('Restart the DRX script?')">Restart DRX Script</button>
        </form>
        <form method="POST" action="{{ url_for('reboot_system_web') }}">
            <button type="submit" onclick="return confirm('Reboot the system?')">Reboot System</button>
        </form>
        <form method="POST" action="{{ url_for('reload_config_web') }}">
            <button type="submit">Reload Configuration File</button>
        </form>
    </div>
    <div class="card-section">
        <h2>Play Specific Track</h2>
        <form method="POST" action="{{ url_for('play_track') }}">
            <label>Track (dropdown):</label>
            <select name="track_dropdown">
                <option value="">--Select--</option>
                {% for file in all_files %}
                    <option value="{{ file }}">{{ file }}</option>
                {% endfor %}
            </select>
            <button type="submit">Play Selected</button>
        </form>
        <form method="POST" action="{{ url_for('play_track') }}">
            <label>Track (input):</label>
            <input name="track_input" type="text" placeholder="e.g. 0001.wav" size="20">
            <button type="submit">Play Input</button>
        </form>
    </div>
    <div class="card-section">
        <h2>Edit config.ini</h2>
        <form method="POST" action="{{ url_for('edit_config') }}">
            <textarea name="config_content" rows="16" cols="80">{{ config_content }}</textarea><br>
            <button type="submit" onclick="return confirm('Save changes to config.ini?')">Save Config</button>
        </form>
    </div>
    <div class="card-section">
        <h2>Recent Serial Commands</h2>
        <div class="serials">
        {% for cmd in serial_history[:5] %}
            <div>{{ cmd }}</div>
        {% endfor %}
        </div>
    </div>
    <div class="card-section">
        <h2>Recent Log Entries</h2>
        <div class="logs">
        {% for entry in web_log[-10:] %}
            <div>{{ entry }}</div>
        {% endfor %}
        </div>
    </div>
</div>
</body>
</html>
'''

LOGIN_TEMPLATE = '''
<!doctype html>
<html>
<head>
<title>Login - DRX Dashboard</title>
<style>
input[type=text], input[type=password] {font-size:1.2em;}
button {font-size:1.1em;}
</style>
</head>
<body>
<h1>DRX Dashboard Login</h1>
<form method="POST" action="{{ url_for('login') }}">
    <label>Username:</label>
    <input type="text" name="username" autofocus required><br>
    <label>Password:</label>
    <input type="password" name="password" required><br>
    <button type="submit">Login</button>
</form>
{% if error %}
<p style="color:red;">{{ error }}</p>
{% endif %}
</body>
</html>
'''

def require_login(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        webuser, webpass = get_web_credentials()
        if username == webuser and password == webpass:
            session['logged_in'] = True
            session['username'] = username
            if "auto_refresh" not in session:
                session["auto_refresh"] = True
            return redirect(url_for('dashboard'))
        else:
            error = "Invalid username or password."
    return render_template_string(LOGIN_TEMPLATE, error=error)

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/", methods=["GET"])
@require_login
def dashboard():
    return render_template_string(DASHBOARD_TEMPLATE,
        currently_playing=currently_playing,
        playback_status=playback_status,
        cos_state="YES" if is_cos_active() else "NO",
        serial_port_missing=serial_port_missing,
        sound_card_missing=sound_card_missing,
        serial_history=serial_history,
        web_log=web_log,
        all_files=get_all_sound_files(),
        config_content=get_config_file_content(),
        session=session
    )

@app.route("/stop", methods=['POST'])
@require_login
def stop_playback():
    playback_interrupt.set()
    web_log.append(f"{datetime.datetime.now()}: Playback stopped from web")
    return redirect(url_for('dashboard'))

@app.route("/playtrack", methods=['POST'])
@require_login
def play_track():
    track = request.form.get("track_dropdown") or request.form.get("track_input", "").strip()
    if track:
        if play_specific_track(track):
            web_log.append(f"{datetime.datetime.now()}: Played track {track} from web")
        else:
            web_log.append(f"{datetime.datetime.now()}: Play failed for track {track} (not found)")
    return redirect(url_for('dashboard'))

@app.route("/restart", methods=['POST'])
@require_login
def restart_script_web():
    web_log.append(f"{datetime.datetime.now()}: DRX script restarted from web")
    threading.Thread(target=restart_script, daemon=True).start()
    time.sleep(1)
    return redirect(url_for('dashboard'))

@app.route("/reboot", methods=['POST'])
@require_login
def reboot_system_web():
    web_log.append(f"{datetime.datetime.now()}: System reboot initiated from web")
    threading.Thread(target=reboot_system, daemon=True).start()
    time.sleep(1)
    return redirect(url_for('dashboard'))

@app.route("/reloadconfig", methods=['POST'])
@require_login
def reload_config_web():
    reload_config()
    web_log.append(f"{datetime.datetime.now()}: Configuration file reloaded from web")
    return redirect(url_for('dashboard'))

@app.route("/editconfig", methods=['POST'])
@require_login
def edit_config():
    content = request.form.get("config_content", "")
    if content:
        save_config_file(content)
        reload_config()
        web_log.append(f"{datetime.datetime.now()}: config.ini edited from web")
    return redirect(url_for('dashboard'))

@app.route("/toggle_refresh", methods=["POST"])
@require_login
def toggle_refresh():
    if "auto_refresh" in session:
        session["auto_refresh"] = not session["auto_refresh"]
    else:
        session["auto_refresh"] = False
    return redirect(url_for('dashboard'))

def run_web_server():
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

def main():
    try:
        threading.Thread(target=serial_read_loop, daemon=True).start()
        threading.Thread(target=process_serial_commands, daemon=True).start()
        threading.Thread(target=run_web_server, daemon=True).start()
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

if __name__ == "__main__":
    main()