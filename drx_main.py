#!/usr/bin/env python3

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
import lgpio
import traceback
import wave
import contextlib
import shutil
import itertools
import json
import string
import inspect
import queue
import uuid
from datetime import datetime, timedelta
from flask import Flask, jsonify
from typing import Optional, Callable, Dict, Any


class PlaybackStatusManager:
    """
    Centralized manager for playback status in DRX.
    
    Handles all status updates through a single interface, maintaining
    thread-safety and providing callbacks to keep legacy global variables
    synchronized.
    """
    
    def __init__(self, write_state_callback: Optional[Callable] = None):
        """
        Initialize the PlaybackStatusManager.
        
        Args:
            write_state_callback: Optional callback function to call after status updates
        """
        self._lock = threading.Lock()
        self._write_state_callback = write_state_callback
        self._status_callbacks: list[Callable] = []
        
        # Internal status state
        self._playback_status = "Idle"
        self._currently_playing = ""
        self._currently_playing_info = ""
        self._currently_playing_info_timestamp = 0
        
    def register_status_callback(self, callback: Callable[[str, str, str, float], None]):
        """
        Register a callback to be called when status changes.
        
        Callback signature: callback(status, playing, info, info_timestamp)
        This is used to sync legacy global variables.
        
        Args:
            callback: Function to call on status updates
        """
        with self._lock:
            self._status_callbacks.append(callback)
    
    def set_status(self, status: str, playing: Optional[str] = None, 
                   info: Optional[str] = None, section_context: Optional[str] = None):
        """
        Set the current playback status with optional context information.
        
        This is the primary method for updating status. It supports:
        - Arbitrary status strings
        - Optional playing track/operation name
        - Optional additional info (displayed for 5 seconds in UI)
        - Section context for base tracks (e.g., "from Rotating Base 5300")
        
        Args:
            status: The main status string (e.g., "Playing", "Echo Test: 1234")
            playing: What is currently playing (defaults to status if not provided)
            info: Additional info to display temporarily
            section_context: Context like "from Rotating Base 5300" to append to info
        """
        with self._lock:
            self._playback_status = status
            self._currently_playing = playing if playing is not None else ""
            
            # Build info string with section context if provided
            if info is not None:
                if section_context:
                    self._currently_playing_info = f"{info} {section_context}"
                else:
                    self._currently_playing_info = info
                self._currently_playing_info_timestamp = time.time()
            else:
                # Only update timestamp if we had existing info and section_context is provided
                if section_context and self._currently_playing_info:
                    self._currently_playing_info = f"{self._currently_playing_info} {section_context}"
                    self._currently_playing_info_timestamp = time.time()
        
        self._notify_callbacks()
        self._call_write_state()
    
    def set_idle(self):
        """
        Set status to idle state (clears all status information).
        """
        with self._lock:
            self._playback_status = "Idle"
            self._currently_playing = ""
            self._currently_playing_info = ""
            self._currently_playing_info_timestamp = 0
        
        self._notify_callbacks()
        self._call_write_state()
    
    def update_info(self, info: str, section_context: Optional[str] = None):
        """
        Update just the info portion without changing main status.
        
        Args:
            info: New info string
            section_context: Optional context to append
        """
        with self._lock:
            if section_context:
                self._currently_playing_info = f"{info} {section_context}"
            else:
                self._currently_playing_info = info
            self._currently_playing_info_timestamp = time.time()
        
        self._notify_callbacks()
        self._call_write_state()
    
    def get_status_info(self) -> Dict[str, Any]:
        """
        Get current status information as a dictionary.
        
        Returns:
            Dict containing current status, playing, info, and timestamp
        """
        with self._lock:
            return {
                'playback_status': self._playback_status,
                'currently_playing': self._currently_playing,
                'currently_playing_info': self._currently_playing_info,
                'currently_playing_info_timestamp': self._currently_playing_info_timestamp
            }
    
    def clear_info_if_expired(self, max_age_seconds: float = 5.0) -> bool:
        """
        Clear info if it has expired based on timestamp.
        
        Args:
            max_age_seconds: Maximum age before info expires
            
        Returns:
            True if info was cleared, False otherwise
        """
        with self._lock:
            if (self._currently_playing_info and 
                time.time() - self._currently_playing_info_timestamp > max_age_seconds):
                self._currently_playing_info = ""
                self._currently_playing_info_timestamp = 0
                
                self._notify_callbacks()
                return True
        return False
    
    def _notify_callbacks(self):
        """Notify all registered status callbacks of the current state."""
        for callback in self._status_callbacks:
            try:
                callback(
                    self._playback_status,
                    self._currently_playing, 
                    self._currently_playing_info,
                    self._currently_playing_info_timestamp
                )
            except Exception as e:
                # Don't let callback errors break status updates
                print(f"Status callback error: {e}")
    
    def _call_write_state(self):
        """Call the write_state callback if configured."""
        if self._write_state_callback:
            try:
                self._write_state_callback()
            except Exception as e:
                # Don't let write_state errors break status updates
                print(f"write_state callback error: {e}")
    
    # Convenience methods for common status patterns
    
    def set_playing(self, filename: str, info: Optional[str] = None, 
                   section_context: Optional[str] = None):
        """Convenience method for setting playing status."""
        import os
        playing_name = os.path.splitext(os.path.basename(filename))[0]
        self.set_status("Playing", playing_name, info or f"Playing {filename}", section_context)
    
    def set_echo_test(self, track_num: int, phase: str = ""):
        """Convenience method for echo test status."""
        status = f"Echo Test: {track_num}"
        if phase:
            status += f" - {phase}"
        self.set_status(status, f"Echo Test: {track_num}", 
                       f"Echo Test {phase} for track {track_num:04d}" if phase else f"Echo Test recording for track {track_num:04d}")
    
    def set_script_execution(self, script_num: str, phase: str = "Running"):
        """Convenience method for script execution status."""
        self.set_status(f"Script: {script_num}", f"Script: {script_num}", 
                       f"{phase} script {script_num}")
    
    def set_weather_report(self, report_type: str, phase: str = "Waiting for channel to clear"):
        """Convenience method for weather report status."""
        self.set_status(f"{report_type}: {phase}" if phase else report_type, 
                       report_type, phase)
     
    def set_activity_report(self, phase: str = "Waiting for channel to clear"):
        """Convenience method for activity report status."""
        self.set_status(f"Activity Report: {phase}" if phase else "Activity Report",
                       "Activity Report", phase)
    
    def set_join_series(self, bases: list, phase: str = "Playing sequence"):
        """Convenience method for join series status."""
        bases_str = '-'.join(str(b) for b in bases)
        self.set_status(f"Join Series: {phase}", f"Join: {bases_str}", phase)
    
    def set_interrupt_sequence(self, from_code: str, to_code: str):
        """Convenience method for interrupt sequence status."""
        self.set_status(f"Interrupt: {from_code} -> {to_code}", 
                       f"{from_code} -> {to_code}",
                       f"Interrupt playback from {from_code} to {to_code}")
    
    def set_waiting_for_cos(self, operation: str = ""):
        """Convenience method for COS waiting status."""
        status = "Waiting for COS to clear"
        if operation:
            status = f"{operation}: {status}"
        self.set_status(status, operation or "Waiting", "Waiting for channel to clear")
    
    def set_pausing(self, item: str = ""):
        """Convenience method for pausing status."""
        self.set_status("Pausing", item, f"Pausing playback" + (f" of {item}" if item else ""))
    
    def set_restarting(self, item: str = ""):
        """Convenience method for restarting status."""
        self.set_status("Restarting", item, f"Pending restart" + (f" of {item}" if item else ""))


# --- Global State Variables ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_LOG_PATH = os.path.join(SCRIPT_DIR, "debug.log")
SCRIPT_NAME = "DRX"
VERSION = "2.01.00"

serial_buffer = ""
serial_history = []
currently_playing = ""
currently_playing_info = ""
currently_playing_info_timestamp = 0
playing_end_time = 0
playback_status = "Idle"
rotation_active = {}
current_playback_thread = None
serial_port_missing = False
sound_card_missing = False
playback_lock = threading.Lock()
remote_device_active = False
cos_active = False
DRX_START_TIME = time.time()
script_dir = os.path.dirname(os.path.realpath(__file__))
config_file_path = os.path.join(script_dir, 'config.ini')
log_file_path = os.path.join(script_dir, 'drx_error.log')
alternate_series_pointers = {}
alternate_series_track_pointers = {}
alternate_series_last_played = {}
alternate_series_state = {}  # key: command string, value: {"pointer": int, "need_to_increment": bool}
message_timer_last_played = 0
message_timer_value = None
last_message_timer_time = 0
command_queue = queue.Queue()
DRX_DIRECTORY = "/home/drx/DRX"
EXTRA_SOUND_DIR = os.path.join(DRX_DIRECTORY, "sounds", "extra")
ACTIVITY_FILE = os.path.join(DRX_DIRECTORY, "logs", "activity.log")
cos_today_seconds = 0
cos_today_minutes = 0
last_written_minutes = -1
DTMF_LOG_FILE = os.path.join(DRX_DIRECTORY, "logs", "dtmf.log")
DTMF_LOG_ARCHIVE_FMT = os.path.join(DRX_DIRECTORY, "dtmf-%Y-%m.log")
dtmf_buffer = {}
dtmf_lock = threading.Lock()
STATE_FILE = os.path.join(DRX_DIRECTORY, "drx_state.json")  # NOTE: No longer used for writes - only for backward compatibility
WEBCMD_FILE = '/tmp/drx_webcmd.json'
LOG_WEB_FILE = os.path.join(DRX_DIRECTORY, "logs", "drx.log")
tot_active = False
tot_start_time = None
tot_last_seconds = 0
tot_lock = threading.Lock()
tot_remote_active = False
last_alert_time = None
ctone_override_expire = 0

# --- In-Memory State for REST API ---
current_state_memory = {}
state_lock = threading.Lock()
prev_currently_playing = ""
last_played_memory = ""

# --- Flask App for State API ---
app = Flask(__name__)
API_PORT = 5000  # Port for state API server
WX_DATA_FILE = "wx/wx_data"
last_cos_active_time = None
Direct = {"enabled": True, "prefix": "P"}
last_written_minutes = -1
DIRECT_ENABLED = True
currently_playing_info_timestamp = 0
rate_limited_set_time = None
rate_limited_timer = None

# --- Status Manager ---
status_manager = None  # Will be initialized in main()

def sync_legacy_status_variables(status, playing, info, info_timestamp):
    """
    Callback function to sync legacy global variables with status manager.
    This maintains backward compatibility with existing code that reads 
    the global variables directly.
    """
    global playback_status, currently_playing, currently_playing_info, currently_playing_info_timestamp
    playback_status = status
    currently_playing = playing
    currently_playing_info = info
    currently_playing_info_timestamp = info_timestamp

# --- Config Loading & Validation ---
config_warnings = []
DEFAULTS = {
    "Sound": {
        "directory": "/tmp/sounds",
        "extension": ".wav",
        "device": "default"
    },
    "GPIO": {
        "cos_pin": 16,
        "cos_activate_level": False,
        "remote_busy_pin": 20,
        "remote_busy_activate_level": False,
        "cos_debounce_time": 0.5,
        "max_cos_interruptions": 3
    },
    "Serial": {
        "port": "/dev/ttyUSB0",
        "baudrate": 9600,
        "timeout": 0.5,
        "line_timeout": 2.0
    },
    "Random": {
        "base": "3000",
        "end": "3099",
        "interval": "10"
    },
    "Rotation": {
        "base": "4000",
        "end": "4099",
        "interval": "10"
    },
    "SudoRandom": {
        "base": "5000",
        "end": "5099",
        "interval": "10"
    },
    "General": {
        "message timer": "N"
    },
    "Debug": {
        "enable_cos_override": False,
        "enable_debug_logging": False
    },
    "Web": {
        "port": 505
    },
    "WebAuth": {
        "username": "",
        "password": ""
    }
}

config = configparser.ConfigParser()
config_ini_missing = False
try:
    found_files = config.read(config_file_path)
    if not found_files:
        config_ini_missing = True
        config_warnings.insert(0, f"config.ini missing at {config_file_path}; using all default values.")
except Exception as e:
    config_ini_missing = True
    config_warnings.insert(0, f"Failed to read config.ini: {e}; using all default values.")

 # --- Utility Functions ---
def str_to_bool(x):
    return str(x).lower() in ('1', 'true', 'yes')
    
def get_config_value(section, key, fallback=None, cast_func=None, warn=None):
    global config_warnings
    # Check if the section exists in the config file
    if not config.has_section(section):
        config_warnings.append(f"Section [{section}] missing; using defaults.")
        val = DEFAULTS[section][key] if section in DEFAULTS and key in DEFAULTS[section] else fallback
        return cast_func(val) if cast_func else val
    # Check if the key exists in the section
    if not config.has_option(section, key):
        config_warnings.append(f"Missing {key} in [{section}]; using default '{DEFAULTS.get(section, {}).get(key, fallback)}'.")
        val = DEFAULTS[section][key] if section in DEFAULTS and key in DEFAULTS[section] else fallback
        return cast_func(val) if cast_func else val
    # Try to get and cast the value
    raw = config[section][key]
    try:
        return cast_func(raw) if cast_func else raw
    except Exception as e:
        config_warnings.append(f"Invalid value for {key} in [{section}]: '{raw}' ({e}); using default '{DEFAULTS.get(section, {}).get(key, fallback)}'.")
        val = DEFAULTS[section][key] if section in DEFAULTS and key in DEFAULTS[section] else fallback
        return cast_func(val) if cast_func else val

ENABLE_DEBUG_LOGGING = get_config_value("Debug", "enable_debug_logging", fallback=False, cast_func=str_to_bool)

def debug_log(*args):
    # Check the value directly from config each time instead of using the global variable
    enable_debug = get_config_value("Debug", "enable_debug_logging", fallback=False, cast_func=str_to_bool)
    if not enable_debug:
        return
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = " ".join(str(a) for a in args)
        formatted_msg = f"[{timestamp}] {msg}"

        if os.path.exists(DEBUG_LOG_PATH):
            try:
                with open(DEBUG_LOG_PATH, 'r') as f:
                    existing_content = f.read()
            except:
                existing_content = ""
        else:
            existing_content = ""

        with open(DEBUG_LOG_PATH, 'w') as f:
            f.write(formatted_msg + '\n' + existing_content)

        os.chmod(DEBUG_LOG_PATH, 0o777)
    except Exception as e:
        print(f"Error in debug_log: {e}")

def parse_message_timer(val):
    val = val.strip().upper()
    if val == "N":
        return "N"
    try:
        return int(val)
    except Exception:
        return "N"

def load_state():
    """
    Load repeater activity state from the activity file (not drx_state.json).
    State file writes are disabled - only activity data is persisted.
    """
    global cos_today_seconds, cos_today_minutes, cos_today_date
    
    # Initialize with current date
    today_str = datetime.now().strftime("%Y-%m-%d")
    cos_today_date = today_str
    
    # Try to load today's activity from the activity file
    cos_today_minutes = parse_minutes_from_activity_log(today_str)
    cos_today_seconds = cos_today_minutes * 60  # Convert back to seconds
    
    debug_log(f"Loaded activity state: {cos_today_minutes} minutes for {cos_today_date}")

def read_state():
    """
    Read state from drx_state.json if it exists.
    Returns empty dict if file doesn't exist (normal behavior now).
    State writes are disabled - this is only for backward compatibility.
    """
    try:
        if not os.path.exists(STATE_FILE):
            return {}
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def get_current_state():
    """
    Get the current state from memory instead of reading from disk.
    Used by web interface and other components that need state access.
    """
    global current_state_memory
    with state_lock:
        return current_state_memory.copy()

def parse_int_list(s, fallback=10, label="", section=""):
    vals = []
    for i, v in enumerate(s.split(',')):
        try:
            if float(v) != int(float(v)):
                config_warnings.append(f"{label} in [{section}]: '{v}' not integer, using {fallback}.")
                vals.append(int(fallback))
            else:
                vals.append(int(float(v)))
        except Exception:
            config_warnings.append(f"{label} in [{section}]: '{v}' invalid, using {fallback}.")
            vals.append(int(fallback))
    return vals

def parse_float_list(s, fallback=10, label="", section=""):
    vals = []
    for i, v in enumerate(s.split(',')):
        try:
            f = float(v)
            if f < 0:
                config_warnings.append(f"{label} in [{section}]: '{v}' < 0, using {fallback}.")
                f = float(fallback)
            elif f != int(f):
                config_warnings.append(f"{label} in [{section}]: '{v}' not integer, using {fallback}.")
                f = float(fallback)
            vals.append(int(f))
        except Exception:
            config_warnings.append(f"{label} in [{section}]: '{v}' invalid, using {fallback}.")
            vals.append(int(fallback))
    return vals

def match_code_file(f, code_str, ext):
    ext = ext.lower()
    f_lower = f.lower()
    original_code_str = code_str
    
    # Remove P prefix if it exists
    if code_str.startswith('P'):
        code_str = code_str[1:]
    
    # Save both versions - with and without leading zeros
    code_with_zeros = code_str
    code_without_zeros = code_str.lstrip('0')
    if code_without_zeros == '':  # Handle case where code is all zeros
        code_without_zeros = '0'
    
    if f_lower.endswith(ext):
        base = f_lower[:-len(ext)]
        
        # Try both with and without leading zeros
        base_without_zeros = base.lstrip('0')
        if base_without_zeros == '':  # Handle case where base is all zeros
            base_without_zeros = '0'
        
        # Match if either version matches
        result = (base == code_with_zeros or
                  base == code_without_zeros or
                  base_without_zeros == code_without_zeros or
                  base.startswith(f"{code_with_zeros}-") or
                  base.startswith(f"{code_without_zeros}-"))
        
        return result
    return False

def validate_config_pairs():
    for bases, ends, label, section in [
        (random_bases, random_ends, "Random", "Random"),
        (rotation_bases, rotation_ends, "Rotation", "Rotation"),
        (sudo_bases, sudo_ends, "SudoRandom", "SudoRandom")
    ]:
        for i, (b, e) in enumerate(zip(bases, ends)):
            if e < b:
                config_warnings.append(f"{label} config: End {e} < Base {b} (index {i})")

def log_error(msg):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_msg = f"[{timestamp}] {msg}"
        if os.path.exists(log_file_path):
            try:
                with open(log_file_path, 'r', encoding='utf-8') as f:
                    existing_content = f.read()
            except Exception:
                existing_content = ""
        else:
            existing_content = ""
        with open(log_file_path, 'w', encoding='utf-8') as f:
            f.write(formatted_msg + '\n' + existing_content)
        try:
            os.chmod(log_file_path, 0o777)
        except Exception:
            pass
    except Exception as e:
        error_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{error_time}] Logging failed: {msg}")
        print(f"Error: {e}")

def log_exception(context: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exc = traceback.format_exc()
    entry = f"[{timestamp}] Exception in {context}:\n{exc}"
    try:
        if os.path.exists(log_file_path):
            try:
                with open(log_file_path, 'r', encoding='utf-8') as f:
                    existing_content = f.read()
            except Exception:
                existing_content = ""
        else:
            existing_content = ""
        with open(log_file_path, 'w', encoding='utf-8') as f:
            f.write(entry + '\n' + existing_content)
        try:
            os.chmod(log_file_path, 0o777)
        except Exception:
            pass
    except Exception:
        print(f"[{timestamp}] Logging exception failed:\n{exc}")

def log_recent(entry):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        with open(LOG_WEB_FILE, "a", encoding='utf-8') as f:
            f.write(f"{ts}: {entry}\n")
        max_size = 500 * 1024
        try:
            if os.path.getsize(LOG_WEB_FILE) > max_size:
                with open(LOG_WEB_FILE, "rb") as f:
                    f.seek(-max_size, os.SEEK_END)
                    data = f.read()
                first_nl = data.find(b'\n')
                if first_nl != -1:
                    data = data[first_nl+1:]
                with open(LOG_WEB_FILE, "wb") as f:
                    f.write(data)
        except Exception:
            pass
    except Exception as e:
        log_error(f"log_recent failed: {e}")

def check_sox_installed():
    if shutil.which("sox") is None:
        log_error("sox is not installed! 'P' mode will not work.")

# ---- GPIO HANDLE GLOBAL ----
LGPIO_CHIP = 0
h = None

def gpio_setup():
    global h
    h = lgpio.gpiochip_open(LGPIO_CHIP)
    # Setup REMOTE_BUSY_PIN as output, initial state = INACTIVE
    lgpio.gpio_claim_output(h, REMOTE_BUSY_PIN)
    # Set the pin to the INACTIVE state according to config
    set_remote_busy(False)
    # Setup COS_PIN as input with pull-up
    lgpio.gpio_claim_input(h, COS_PIN, lgpio.SET_PULL_UP)

def gpio_cleanup():
    global h
    if h is not None:
        lgpio.gpiochip_close(h)
        h = None

def command_processor_loop():
    worker_id = str(uuid.uuid4())[:8]
    debug_log(f"Worker {worker_id}: starting")
    while True:
        cmd = command_queue.get()
        debug_log(f"Worker {worker_id}: Processing command: {cmd}")
        process_command(cmd)
        command_queue.task_done()

def is_cos_active():
    override_enabled = config.getboolean('Debug', 'enable_cos_override', fallback=False) if config.has_section('Debug') else False
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
        global h
        COS_ACTIVE_LEVEL = config.getboolean('GPIO', 'cos_activate_level', fallback=False)
        level = int(lgpio.gpio_read(h, COS_PIN))
        return (level == int(COS_ACTIVE_LEVEL))
    except Exception:
        log_exception("is_cos_active (lgpio.gpio_read)")
        return False
      
def set_remote_busy(is_busy):
    """
    Set the REMOTE_BUSY_PIN to busy (True) or idle (False), using hardware logic inversion if needed.
    Change the logic in this function if you ever swap hardware/relay sense!
    """
    global h
    # Invert here if your hardware is "backwards" (set True to LOW, False to HIGH)
    desired_level = int(not REMOTE_BUSY_ACTIVE_LEVEL) if is_busy else REMOTE_BUSY_ACTIVE_LEVEL
    lgpio.gpio_write(h, REMOTE_BUSY_PIN, desired_level)
    actual = lgpio.gpio_read(h, REMOTE_BUSY_PIN)
    debug_log(f"set_remote_busy({is_busy}): wrote {desired_level}, pin now reads {actual}")

def is_remote_busy_active():
    """
    Returns True when the REMOTE_BUSY_PIN is in the 'busy' hardware state.
    """
    global h
    level = lgpio.gpio_read(h, REMOTE_BUSY_PIN)
    # No inversion needed: always return True when pin == REMOTE_BUSY_ACTIVE_LEVEL
    return level != REMOTE_BUSY_ACTIVE_LEVEL  

def monitor_cos():
    global last_cos_active_time
    prev_state = False
    while True:
        curr_state = is_cos_active()
        if curr_state and not prev_state:
            last_cos_active_time = time.time()
        prev_state = curr_state
        time.sleep(0.1)

def parse_suffixes(cmd):
    valid_suffixes = {'I', 'R', 'P', 'M', 'W'}  # Only upper-case W allowed
    idx = len(cmd)
    suffixes = ""
    # Walk backwards until no more valid suffixes
    while idx > 0 and cmd[idx-1] in valid_suffixes:
        suffixes = cmd[idx-1] + suffixes
        idx -= 1
    return cmd[:idx], suffixes if suffixes else None

def parse_alternate_series_segments(cmd):
    """
    Splits an alternate series command of the form P5300RA5400i6000A2801PA9300I into
    segments: ['P5300R', 'P5400i6000', 'P2801P', 'P9300I']
    """
    cmd = cmd.strip()
    if not cmd.startswith('P'):
        return []
    segments = []
    curr = ''
    for i, c in enumerate(cmd):
        if i == 0:   # First char (should be P)
            curr += c
        elif c == 'A':
            segments.append(curr)
            curr = 'P'
        else:
            curr += c
    if curr:
        segments.append(curr)
    return segments

def handle_alternate_series_new(command):
    """
    In-memory alternate series logic: each call only evaluates one segment as a standalone command.
    """
    series_key = command.strip().upper()
    segments = parse_alternate_series_segments(series_key)
    if not segments or len(segments) < 2:
        # Not a valid alternate series
        return False

    state = alternate_series_state.setdefault(series_key, {"pointer": 0, "need_to_increment": False})
    pointer = state["pointer"]
    n_segments = len(segments)

    # Advance pointer if "need_to_increment" is True
    if state["need_to_increment"]:
        pointer = (pointer + 1) % n_segments
        state["pointer"] = pointer

    # Play only the current segment, as a standalone command
    current_segment = segments[pointer]
    debug_log(f"[ALT SERIES] Evaluating segment {pointer+1}/{n_segments}: {current_segment}")
    process_command(current_segment)

    # After playback, set to increment next time
    state["pointer"] = pointer
    state["need_to_increment"] = True

    return True

def parse_join_series(cmd):
    """
    Parse a Join-series command, e.g. P1001JR2002IM or P1001J2002J3003M
    Returns:
        bases:     list of int base codes (e.g., [1001, 2002])
        suffixes:  list of str suffixes per base (e.g., ['R', 'IM'])
        overall_m: bool, True if trailing M after last code (removed from suffix)
        is_join:   True if detected
    Rules:
        - J (uppercase) is the separator
        - Only M can be an overall suffix (after the last code/suffix)
        - All other suffixes (R, I, P, W, etc) after a code apply only to that code
    """
    cmd = cmd.strip()
    # Only process Join if uppercase J present
    if not re.search(r'J', cmd):
        return [], [], False, False

    # Remove leading P if present
    if cmd.startswith('P'):
        cmd = cmd[1:]

    # Split by uppercase J
    parts = re.split(r'J', cmd)
    bases = []
    suffixes = []

    # For each part: digits then suffixes
    for part in parts:
        m = re.match(r'^(\d{4})([A-Z]*)$', part, re.IGNORECASE)
        if m:
            bases.append(int(m.group(1)))
            suffixes.append(m.group(2) or "")
        else:
            # malformed, not a J series
            return [], [], False, False

    # Check for trailing M after the last suffix
    last_suffix = suffixes[-1]
    overall_m = False
    if last_suffix.upper().endswith('M'):
        overall_m = True
        suffixes[-1] = last_suffix[:-1]  # Remove the M from last code's suffix

    return bases, suffixes, overall_m, True

def handle_join_series(bases, suffixes, overall_m):
    """
    Sequentially play a list of wavs/sections with per-base suffixes, holding REMOTE_BUSY active.
    If overall_m is True, treat as one message for timer logic.
    Section bases (Random, Rotation, SudoRandom) are played using play_any_section_by_type.
    Direct base codes use play_direct_track.

    This version processes each base+suffix as an individual DRX command, ensuring
    suffixes (R, P, I, M, etc.) are respected per segment, and message timer logic applies per-segment.
    """
    global playback_status, currently_playing, currently_playing_info, currently_playing_info_timestamp
    global message_timer_last_played, message_timer_value

    # If overall_m is set (M at the end of the join), enforce message timer for the whole series
    if overall_m:
        should_play = should_allow_message_timer_play(True, message_timer_value, message_timer_last_played)
        if not should_play:
            set_message_rate_limited()
            return
        else:
            # Set timer as used
            message_timer_last_played = time.time()

    try:
        cancel_rate_limited_timer()
        set_remote_busy(True)
        status_manager.set_join_series(bases)

        # For each base+suffix, build the DRX command and process individually
        for i, base in enumerate(bases):
            suf = suffixes[i].upper() if i < len(suffixes) and suffixes[i] else ""
            cmd = f"P{base:04d}{suf}"
            process_command(cmd)
            # Do not clear REMOTE_BUSY here; keep it active for the entire series

        status_manager.set_idle()
    finally:
        set_remote_busy(False)

def get_duration_wav(filename):
    try:
        with contextlib.closing(wave.open(filename, 'r')) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            duration = frames / float(rate)
            return duration
    except Exception:
        return 0

def process_command(command):
    global playback_interrupt, currently_playing, currently_playing_info, currently_playing_info_timestamp, playback_status
    global message_timer_last_played, message_timer_value
    global cos_today_seconds, cos_today_date
    global last_cos_active_time
    global rate_limited_timer

    debug_log("process_command reached")
    debug_log(f"RAW process_command input: {repr(command)}")
    try:
        # --- TOT/TOP Time Out Timer logic ---
        if command.strip().upper() == "TOT":
            debug_log("TOT command received in process_command.")
            handle_tot_start()
            return
        if command.strip().upper() == "TOP":
            debug_log("TOP command received in process_command.")
            handle_top_command()
            return

        cmd = command.strip().upper()

        # --- Repeater Activity A1 Command: Speak previous day's activity minutes ---
        if cmd == "A1":
            cancel_rate_limited_timer()
            speak_activity_minutes_for_previous_day()
            return

        # --- Forced WX: Always play WX regardless of COS (e.g., W1F) ---
        if cmd == "W1F":
            cancel_rate_limited_timer()
            debug_log("W1F command received: Forcing WX report, ignoring COS activity.")
            speak_wx_conditions()
            return

        # --- Speak WX Conditions W1 Command ---
        if cmd == "W1":
            cancel_rate_limited_timer()
            # Check if COS was active in the last 10 seconds
            if last_cos_active_time is not None and (time.time() - last_cos_active_time) <= 10:
                debug_log("COS was active within the last 10 seconds. Jumping to W2 command instead.")
                speak_temperature()
                return
            speak_wx_conditions()
            return

        # --- Speak Temperature W2 Command ---
        if cmd == "W2":
            cancel_rate_limited_timer()
            speak_temperature()
            return
        
        # --- WX Alerts W3 Command ---
        if cmd == "W3":
            cancel_rate_limited_timer()
            debug_log("W3 command - Weather alerts")
            speak_wx_alerts()
            return

        # --- Repeater Activity Reset Command: ARST (example) ---
        if command.strip().upper() == "ARST":
            cancel_rate_limited_timer()
            cos_today_seconds = 0
            write_state()
            prepend_or_replace_today_entry(cos_today_date, 0)
            log_recent("Repeater Activity minutes reset for current day by command.")
            return

        # Check for Echo Test command (RE9999 format)
        if command.upper().startswith('RE') and len(command) >= 6:
            try:
                cancel_rate_limited_timer()
                track_num = int(command[2:].strip())
                debug_log(f"Echo Test command detected with track number: {track_num}")
                echo_test(track_num)

                # Add to history
                serial_history.insert(0, {
                    "cmd": f"Echo Test: {track_num:04d}",
                    "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "src": "Command"
                })
                log_recent(f"Echo Test started for track {track_num}")
                return
            except ValueError:
                debug_log(f"Invalid Echo Test command format: {command}")

        # Check for Script command (S1001 format)
        if command.upper().startswith('S') and len(command) > 1:
            try:
                cancel_rate_limited_timer()
                script_num = command[1:].strip()
                debug_log(f"Script command detected: {script_num}")
                # Run script directly
                run_script(script_num)

                # Add to history
                serial_history.insert(0, {
                    "cmd": f"Script: {script_num}",
                    "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "src": "Command"
                })
                log_recent(f"Script execution started: {script_num}")
                return
            except Exception as e:
                debug_log(f"Invalid script command format or execution error: {e}")
                log_exception("script_command")

        # --- Play by filename if .wav ---
        if command.lower().endswith('.wav'):
            cancel_rate_limited_timer()
            filename = os.path.join(SOUND_DIRECTORY, command)
            if os.path.isfile(filename):
                play_sound(filename=filename)
            else:
                status_manager.set_idle()
            return

        # --- Join series logic ---
        bases, suffixes, overall_m, is_join = parse_join_series(command.strip())
        if is_join and bases:
            cancel_rate_limited_timer()
            handle_join_series(bases, suffixes, overall_m)
            return

        # --- Alternate series logic (NEW) ---
        if 'A' in command.strip().upper():
            cancel_rate_limited_timer()
            if handle_alternate_series_new(command.strip()):
                return

        # --- Serial (direct) and section logic ---
        code_str, suffix, alt_code = parse_serial_command(command.strip())
        if code_str is None:
            cancel_rate_limited_timer()
            status_manager.set_idle()
            return

        # DO NOT uppercase the suffix here!
        interruptible = "I" in suffix if suffix else False
        repeat = "R" in suffix if suffix else False
        pausing = "P" in suffix if suffix else False
        message_mode = "M" in suffix if suffix else False
        wait_for_cos = "W" in suffix if suffix else False

        # --- Fix: Pause and Repeat cannot both be True, pause takes precedence ---
        if repeat and pausing:
            debug_log("Both repeat and pausing True! Forcing pause to take precedence.")
            repeat = False

        code = int(code_str)

        should_play_message = should_allow_message_timer_play(message_mode, message_timer_value, message_timer_last_played)
        if message_mode and not should_play_message:
            set_message_rate_limited()
            return

        if message_mode and should_play_message:
            message_timer_last_played = time.time()

        # COS-i logic (interrupt to another code)
        if suffix == 'i' and alt_code is not None:
            base_filename = get_next_base_file(code)
            if not base_filename:
                cancel_rate_limited_timer()
                status_manager.set_idle()
                return
            play_interrupt_to_another(base_filename, str(alt_code))
            status_manager.set_interrupt_sequence(str(code), str(alt_code))
            return

        # Random Section
        for b, e, t in zip(random_bases, random_ends, random_intervals):
            if code == b:
                cancel_rate_limited_timer()
                play_randomized_section(
                    b, e, t * 60, random_last_played, random_current_track,
                    interruptible, pausing, repeat, wait_for_cos=wait_for_cos
                )
                return
            elif b < code <= e:
                cancel_rate_limited_timer()
                play_direct_track(code_str, interruptible, pausing, repeat, wait_for_cos=wait_for_cos)
                return

        # Rotating Section
        for b, e, t in zip(rotation_bases, rotation_ends, rotation_times):
            if code == b:
                cancel_rate_limited_timer()
                if not rotation_active.get(b, False):
                    rotation_active[b] = True
                    play_rotating_section(
                        b, e, t * 60, rotation_last_played, rotation_current_track,
                        interruptible, pausing, repeat, wait_for_cos=wait_for_cos
                    )
                else:
                    debug_log(f"Rotation for base {b} is already active, ignoring repeat trigger.")
                return
            elif b < code <= e:
                cancel_rate_limited_timer()
                play_direct_track(code_str, interruptible, pausing, repeat, wait_for_cos=wait_for_cos)
                return

        # SudoRandom Section
        for b, e, t in zip(sudo_bases, sudo_ends, sudo_intervals):
            if code == b:
                cancel_rate_limited_timer()
                play_sudo_random_section(
                    b, e, t * 60,
                    sudo_random_last_interval,
                    sudo_random_interval_track,
                    sudo_random_played_in_cycle,
                    interruptible, pausing, repeat, wait_for_cos=wait_for_cos
                )
                return
            elif b < code <= e:
                cancel_rate_limited_timer()
                play_direct_track(code_str, interruptible, pausing, repeat, wait_for_cos=wait_for_cos)
                return

        # Direct section (default)
        if DIRECT_ENABLED:
            cancel_rate_limited_timer()
            play_direct_track(code_str, interruptible, pausing, repeat, wait_for_cos=wait_for_cos)
        else:
            cancel_rate_limited_timer()
            status_manager.set_idle()
    except Exception:
        log_exception("process_command")

def detect_section_context(filename):
    """
    Detect if a filename belongs to a configured section and return appropriate context.
    Returns a string like "from Rotating Base 5300" or None if not a base track.
    """
    import os
    import re

    # Extract base track number from filename (handles e.g. "5308-Title.wav", "P5300A5400-Title.wav")
    basename = os.path.basename(filename)
    track_name = os.path.splitext(basename)[0]

    # Extract numeric prefix (handles 5308-Title, P5300A5400-Title, P5300J5400-Title, etc.)
    m = re.match(r'P?(\d+)', track_name)
    if not m:
        return None
    track_num = int(m.group(1))

    # Check if this track belongs to any configured section
    # Random sections
    for i, (base, end, interval) in enumerate(zip(random_bases, random_ends, random_intervals)):
        if base <= track_num <= end:
            return f"from Random Base {base}"

    # Rotation sections  
    for i, (base, end, time_val) in enumerate(zip(rotation_bases, rotation_ends, rotation_times)):
        if base <= track_num <= end:
            return f"from Rotating Base {base}"

    # SudoRandom sections
    for i, (base, end, interval) in enumerate(zip(sudo_bases, sudo_ends, sudo_intervals)):
        if base <= track_num <= end:
            return f"from SudoRandom Base {base}"

    return None

def handle_alternate_series(command):
    bases, suffixes, is_alt = parse_alternate_series(command)
    debug_log(f"ALTERNATE DEBUG: command={command}, bases={bases}, suffixes={suffixes}, is_alt={is_alt}")
    if not is_alt or not bases:
        debug_log("ALTERNATE DEBUG: Not an alternate command or no bases")
        return False

    key = tuple(bases)
    if key not in alternate_series_pointers:
        alternate_series_pointers[key] = 0

    pointer = alternate_series_pointers[key]
    base_to_play = bases[pointer]
    base_suffix = suffixes[pointer] if pointer < len(suffixes) else ""
    debug_log(f"ALTERNATE DEBUG: pointer={pointer}, base_to_play={base_to_play}, base_suffix={base_suffix}")

    repeat = 'R' in base_suffix
    pausing = 'P' in base_suffix
    interruptible = 'I' in base_suffix
    wait_for_cos = 'W' in base_suffix

    code_str = f"{base_to_play:04d}"
    debug_log(f"ALTERNATE DEBUG: code_str={code_str}, repeat={repeat}, pausing={pausing}, interruptible={interruptible}, wait_for_cos={wait_for_cos}")

    debug_log("About to call play_direct_track")
    # PATCH: Always pass a display_name with context for alternating series!
    # Find the file we will play, extract track_num and title for formatting.
    base_filename = get_next_base_file(base_to_play)
    if base_filename:
        import os
        track_filename = os.path.basename(base_filename)
        # Extract track_num and title
        # Handles "5308-Title.wav" or "P5300A5400-Title.wav"
        m = re.match(r'P?(\d+)', track_filename)
        track_num = m.group(1) if m else ""
        title = ""
        if "-" in track_filename:
            title = track_filename.split("-", 1)[1].rsplit(".", 1)[0]
        # Detect section type for more accurate context
        section_context = detect_section_context(base_filename)
        # Guess base_type for display
        base_type = ""
        if section_context:
            if "Rotating" in section_context:
                base_type = "Rotating Base"
            elif "Random" in section_context:
                base_type = "Random Base"
            elif "SudoRandom" in section_context:
                base_type = "SudoRandom Base"
            else:
                base_type = section_context
        else:
            base_type = "Base"
        base_num = base_to_play
        def format_currently_playing(track_num, title, base_type, base_num):
            if title:
                return f"{track_num}-{title} from {base_type} {base_num}"
            else:
                return f"{track_num} from {base_type} {base_num}"
        currently_playing_str = format_currently_playing(track_num, title, base_type, base_num)
        play_sound(
            filename=base_filename,
            interruptible=interruptible,
            pausing=pausing,
            repeating=repeat,
            wait_for_cos=wait_for_cos,
            display_name=currently_playing_str
        )
        alternate_series_pointers[key] = (pointer + 1) % len(bases)
        debug_log(f"ALTERNATE DEBUG: next pointer will be {alternate_series_pointers[key]}")
        return True
    else:
        debug_log(f"ALTERNATE DEBUG: No base file found for {base_to_play}")
        return False

def play_sound(
    filename,
    interruptible=False,
    pausing=False,
    repeating=False,
    wait_for_cos=False,
    playback_token=None,
    display_name=None
):
    """
    Enhanced play_sound with true pause/resume for pausing mode using sox+aplay.
    All status_manager, debug_log, and modern DRX features retained.
    """
    debug_log(f"[PLAY_SOUND DEBUG] filename={filename}, display_name={display_name}")
    import os
    import time
    import subprocess
    global currently_playing, currently_playing_info, currently_playing_info_timestamp, playing_end_time
    global playback_interrupt, playback_status, sound_card_missing, current_playback_token, MAX_COS_INTERRUPTIONS
    global h

    # --- DEBUG: Show what file is about to play and if it exists ---
    debug_log(f"play_sound: absolute filename to play: {os.path.abspath(filename)}")
    debug_log(f"play_sound: exists? {os.path.exists(filename)}")

    subprocess.run(['alsactl', 'restore'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    debug_log(f"play_sound: filename={filename} interruptible={interruptible} pausing={pausing} repeating={repeating} wait_for_cos={wait_for_cos}")

    section_context = detect_section_context(filename)
    playing_name = display_name if display_name else os.path.splitext(os.path.basename(filename))[0]
    status_manager.set_status("Playing", playing_name, None, section_context)

    success = False
    interrupted = False

    try:
        if wait_for_cos:
            status_manager.set_waiting_for_cos()
            debug_log("WAIT FOR COS MODE ACTIVE (W suffix)")

            COS_DEBOUNCE_TIME = get_config_value("GPIO", "cos_debounce_time", fallback=0.5, cast_func=float)

            while True:
                if is_cos_active():
                    debug_log("WaitForCOS: Waiting for COS to become inactive")
                    while is_cos_active():
                        time.sleep(0.1)
                        if playback_token is not None and playback_token != current_playback_token:
                            interrupted = True
                            return
                        if playback_interrupt.is_set():
                            interrupted = True
                            return
                debug_log("WaitForCOS: COS has become inactive, starting debounce timer")
                debounce_start = time.time()
                while time.time() - debounce_start < COS_DEBOUNCE_TIME:
                    if is_cos_active():
                        debug_log("WaitForCOS: COS became active again during debounce period, restarting wait process")
                        break
                    if playback_token is not None and playback_token != current_playback_token:
                        interrupted = True
                        return
                    if playback_interrupt.is_set():
                        interrupted = True
                        return
                    time.sleep(0.1)
                if time.time() - debounce_start >= COS_DEBOUNCE_TIME:
                    debug_log(f"WaitForCOS: Successfully waited through full debounce period of {COS_DEBOUNCE_TIME} seconds")
                    break

            debug_log(f"Setting REMOTE_BUSY to {REMOTE_BUSY_ACTIVE_LEVEL} (wait_for_cos mode - play)")
            set_remote_busy(True)
            status_manager.set_status("Playing (WaitForCOS Mode)", playing_name, None, section_context)

            proc = None
            try:
                proc = subprocess.Popen(
                    ['aplay', '-D', SOUND_DEVICE, filename],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE
                )
                while proc.poll() is None:
                    if playback_token is not None and playback_token != current_playback_token:
                        proc.terminate()
                        interrupted = True
                        break
                    if interruptible and is_cos_active():
                        debug_log("WaitForCOS: COS became ACTIVE, interrupting playback")
                        proc.terminate()
                        time.sleep(0.2)
                        if proc.poll() is None:
                            proc.kill()
                        interrupted = True
                        break
                    if playback_interrupt.is_set():
                        proc.terminate()
                        interrupted = True
                        break
                    time.sleep(0.05)
                if not interrupted:
                    success = True
            except Exception as e:
                debug_log("Exception starting aplay (WaitForCOS):", e)
                interrupted = True
            finally:
                if proc:
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
                        try:
                            proc.wait(timeout=2)
                        except Exception:
                            pass
                if proc and proc.stderr:
                    err = proc.stderr.read()
                    if err:
                        debug_log(f"aplay error: {err.decode(errors='replace')}")
        elif repeating:
            status_manager.set_status("Playing (Repeat Mode)", playing_name, None, section_context)
            debug_log("REPEAT MODE ACTIVE")
            cos_interruptions = 0
            ignore_cos = False
            while True:
                if not ignore_cos:
                    while is_cos_active() and not playback_interrupt.is_set():
                        status_manager.set_restarting(playing_name)
                        debug_log(f"Setting REMOTE_BUSY to {REMOTE_BUSY_ACTIVE_LEVEL} (repeat - pending)")
                        set_remote_busy(True)
                        time.sleep(0.05)
                        if playback_token is not None and playback_token != current_playback_token:
                            interrupted = True
                            return
                    status_manager.set_status("Playing (Repeat Mode)", playing_name, None, section_context)
                debug_log(f"Setting REMOTE_BUSY to {REMOTE_BUSY_ACTIVE_LEVEL} (repeat - play)")
                set_remote_busy(True)
                try:
                    proc = subprocess.Popen(
                        ['aplay', '-D', SOUND_DEVICE, filename],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE
                    )
                except Exception as e:
                    debug_log("Exception in REPEAT mode (Popen):", e)
                    import traceback
                    traceback.print_exc()
                    break

                was_interrupted = False
                while proc.poll() is None:
                    if playback_token is not None and playback_token != current_playback_token:
                        proc.terminate()
                        was_interrupted = True
                        interrupted = True
                        break
                    if not ignore_cos and is_cos_active():
                        cos_interruptions += 1
                        debug_log(f"Repeat mode: COS interruptions so far: {cos_interruptions}")
                        if cos_interruptions >= MAX_COS_INTERRUPTIONS:
                            debug_log("Repeat mode: max_cos_interruptions reached, will ignore COS from now on. Letting current playback continue.")
                            ignore_cos = True
                            break
                        debug_log("COS active: stopping and will repeat")
                        proc.terminate()
                        time.sleep(0.1)
                        if proc.poll() is None:
                            proc.kill()
                        was_interrupted = True
                        interrupted = True
                        break
                    if playback_interrupt.is_set():
                        proc.terminate()
                        was_interrupted = True
                        interrupted = True
                        break
                    time.sleep(0.05)
                if was_interrupted and proc.poll() is None:
                    proc.kill()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                if proc.stderr:
                    err = proc.stderr.read()
                    if err:
                        debug_log(f"aplay error: {err.decode(errors='replace')}")
                if ignore_cos:
                    debug_log("WAV played all the way through (final allowed play), ending repeat mode.")
                    success = True
                    break
                if not was_interrupted:
                    debug_log("WAV played all the way through, ending repeat mode.")
                    success = True
                    break
                if playback_interrupt.is_set() or (playback_token is not None and playback_token != current_playback_token):
                    break

        elif pausing:
            status_manager.set_status("Playing (Pause Mode)", playing_name, None, section_context)
            debug_log("PAUSE MODE ACTIVE")
            from contextlib import closing
            import wave
            # True pause/resume: keep track of how much played, restart at correct offset
            try:
                # Get total duration of file
                with closing(wave.open(filename, 'r')) as f:
                    frames = f.getnframes()
                    rate = f.getframerate()
                    total_duration = frames / float(rate)
            except Exception:
                total_duration = 0
            played_duration = 0
            cos_interruptions = 0
            max_interrupts = MAX_COS_INTERRUPTIONS if 'MAX_COS_INTERRUPTIONS' in globals() else 3
            while played_duration < total_duration:
                # Build sox command to trim from played_duration
                sox_cmd = [
                    'sox', filename, '-t', 'wav', '-', 'trim', f'{played_duration}'
                ]
                debug_log(f"PAUSE MODE: sox_cmd={' '.join(str(x) for x in sox_cmd)} (played_duration={played_duration:.2f}/{total_duration:.2f})")
                try:
                    proc1 = subprocess.Popen(sox_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    proc2 = subprocess.Popen(['aplay', '-D', SOUND_DEVICE], stdin=proc1.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    proc1.stdout.close()
                except FileNotFoundError:
                    sound_card_missing = True
                    currently_playing_info = "Sound card/device missing or aplay/sox not found!"
                    status_manager.set_idle()
                    return
                except Exception as exc:
                    sound_card_missing = True
                    currently_playing_info = f"Sound card/device error: {exc}"
                    status_manager.set_idle()
                    return
                interrupted = False
                start_time = time.time()
                status_manager.set_status("Playing (Pause Mode)", playing_name, None, section_context)
                set_remote_busy(True)
                while proc2.poll() is None:
                    if playback_token is not None and playback_token != current_playback_token:
                        proc2.terminate()
                        proc1.terminate()
                        interrupted = True
                        break
                    if is_cos_active():
                        if cos_interruptions < max_interrupts:
                            status_manager.set_pausing(playing_name)
                            debug_log("Pause mode: COS became ACTIVE, pausing playback")
                            proc2.terminate()
                            proc1.terminate()
                            time.sleep(0.1)
                            if proc2.poll() is None:
                                proc2.kill()
                            if proc1.poll() is None:
                                proc1.kill()
                            cos_interruptions += 1
                            interrupted = True
                            # Add how much played this segment
                            played_duration += time.time() - start_time
                            debug_log(f"PAUSE MODE: interrupted, new played_duration={played_duration:.2f}")
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
                            played_duration = total_duration  # Stop playback
                            status_manager.set_idle()
                            return
                    if playback_interrupt.is_set():
                        proc2.terminate()
                        proc1.terminate()
                        interrupted = True
                        break
                    time.sleep(0.05)
                # Clean up after interruption or finish
                if proc2.poll() is None:
                    proc2.kill()
                if proc1.poll() is None:
                    proc1.kill()
                if not interrupted or cos_interruptions >= max_interrupts or playback_interrupt.is_set():
                    debug_log("PAUSE MODE: ending playback (not interrupted or max interrupts reached)")
                    break
            # If we reach here, playback completed
            if played_duration >= total_duration:
                debug_log("PAUSE MODE: played entire file, ending pause mode.")
                success = True

        elif interruptible:
            status_manager.set_status("Playing (Interruptible Mode)", playing_name, None, section_context)
            debug_log("INTERRUPTIBLE MODE ACTIVE")
            debug_log(f"Setting REMOTE_BUSY to {REMOTE_BUSY_ACTIVE_LEVEL} (interruptible - play)")
            set_remote_busy(True)
            try:
                proc = subprocess.Popen(
                    ['aplay', '-D', SOUND_DEVICE, filename],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE
                )
                while proc.poll() is None:
                    if playback_token is not None and playback_token != current_playback_token:
                        proc.terminate()
                        interrupted = True
                        break
                    if is_cos_active():
                        debug_log("INTERRUPTIBLE MODE: COS became ACTIVE, interrupting playback")
                        proc.terminate()
                        time.sleep(0.2)
                        if proc.poll() is None:
                            proc.kill()
                        interrupted = True
                        break
                    if playback_interrupt.is_set():
                        proc.terminate()
                        interrupted = True
                        break
                    time.sleep(0.05)
                if not interrupted:
                    success = True
            except Exception as e:
                debug_log("Exception starting aplay (Interruptible Mode):", e)
                interrupted = True
            finally:
                if proc:
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
                        try:
                            proc.wait(timeout=2)
                        except Exception:
                            pass
                if proc and proc.stderr:
                    err = proc.stderr.read()
                    if err:
                        debug_log(f"aplay error: {err.decode(errors='replace')}")
        else:
            status_manager.set_status("Playing (Normal Mode)", playing_name, None, section_context)
            debug_log("NORMAL MODE ACTIVE")
            debug_log(f"Setting REMOTE_BUSY to {REMOTE_BUSY_ACTIVE_LEVEL} (normal - play)")
            set_remote_busy(True)
            try:
                proc = subprocess.Popen(
                    ['aplay', '-D', SOUND_DEVICE, filename],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE
                )
                while proc.poll() is None:
                    if playback_token is not None and playback_token != current_playback_token:
                        proc.terminate()
                        interrupted = True
                        break
                    if interruptible and is_cos_active():
                        debug_log("NORMAL MODE: COS became ACTIVE, interrupting playback")
                        proc.terminate()
                        time.sleep(0.2)
                        if proc.poll() is None:
                            proc.kill()
                        interrupted = True
                        break
                    if playback_interrupt.is_set():
                        proc.terminate()
                        interrupted = True
                        break
                    time.sleep(0.05)
                if not interrupted:
                    success = True
            except Exception as e:
                debug_log("Exception starting aplay (Normal Mode):", e)
                interrupted = True
            finally:
                if proc:
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
                        try:
                            proc.wait(timeout=2)
                        except Exception:
                            pass
                if proc and proc.stderr:
                    err = proc.stderr.read()
                    if err:
                        debug_log(f"aplay error: {err.decode(errors='replace')}")
    finally:
        status_manager.set_idle()
        debug_log(f"Clearing REMOTE_BUSY to {int(not REMOTE_BUSY_ACTIVE_LEVEL)} (finally block)")
        set_remote_busy(False)
        if playback_interrupt.is_set():
            playback_interrupt.clear()
        play_mode = 'repeat' if repeating else 'pause' if pausing else 'wait_for_cos' if wait_for_cos else 'interruptible' if interruptible else 'normal'
        if success:
            log_recent(f"Play: {os.path.basename(filename)} [{play_mode}] - successful")
        elif interrupted:
            log_recent(f"Play: {os.path.basename(filename)} [{play_mode}] - interrupted")
        else:
            log_recent(f"Play: {os.path.basename(filename)} [{play_mode}] - playback error")

def play_single_wav(
    code,
    interrupt_on_cos=False,
    block_interrupt=False,
    playback_token=None,
    wait_for_cos=False,
    reset_status_on_end=True,
    set_status_on_play=True
):
    import os
    global playback_status, currently_playing, currently_playing_info, currently_playing_info_timestamp
    global ctone_override_expire

    wx_alerts = config.getboolean('WX', 'alerts', fallback=False)
    ctone = config.get('WX', 'ctone', fallback='').strip()
    now = time.time()
    code_str = code
    import re
    if isinstance(code, str) and not code.lower().endswith('.wav') and not os.path.isfile(code):
        if wx_alerts and ctone and ctone.isdigit() and len(ctone) == 4 and now < ctone_override_expire:
            m = re.match(r'^(\d{4})([A-Z]*)-CT\b.*', code, re.IGNORECASE)
            if m:
                suffix = m.group(2)
                new_code_str = ctone + suffix
                log_recent(f"CT Override {filebase} -> {new_code_str}.")
                debug_log(f"CTONE OVERRIDE: play_single_wav substituting {code} with {new_code_str} (active; expires at {ctone_override_expire})")
                code_str = new_code_str

    if isinstance(code_str, str) and code_str.lower().endswith('.wav') and os.path.isfile(code_str):
        filename = code_str
    else:
        matches = [
            f for f in os.listdir(SOUND_DIRECTORY)
            if match_code_file(f, code_str, SOUND_FILE_EXTENSION)
        ]
        if not matches:
            debug_log(f"File {code_str}{SOUND_FILE_EXTENSION} not found.")
            if reset_status_on_end:
                status_manager.set_idle()
            return False
        filename = os.path.join(SOUND_DIRECTORY, matches[0])

    debug_log(
        f"play_single_wav: filename={filename}, interrupt_on_cos={interrupt_on_cos}, block_interrupt={block_interrupt}, wait_for_cos={wait_for_cos}"
    )
    if wait_for_cos:
        COS_DEBOUNCE_TIME = get_config_value("GPIO", "cos_debounce_time", fallback=0.5, cast_func=float)
        debug_log("wait_for_cos: Waiting for COS to become inactive")
        while is_cos_active():
            if playback_token is not None and playback_token != current_playback_token:
                if reset_status_on_end:
                    status_manager.set_idle()
                return False
            if not block_interrupt and playback_interrupt.is_set():
                if reset_status_on_end:
                    status_manager.set_idle()
                return False
            time.sleep(0.1)
        debug_log("wait_for_cos: COS inactive, starting debounce")
        debounce_start = time.time()
        while time.time() - debounce_start < COS_DEBOUNCE_TIME:
            if is_cos_active():
                debug_log("wait_for_cos: COS became active during debounce, restarting wait")
                while is_cos_active():
                    if playback_token is not None and playback_token != current_playback_token:
                        if reset_status_on_end:
                            status_manager.set_idle()
                        return False
                    if not block_interrupt and playback_interrupt.is_set():
                        if reset_status_on_end:
                            status_manager.set_idle()
                        return False
                    time.sleep(0.1)
                debounce_start = time.time()
            if playback_token is not None and playback_token != current_playback_token:
                if reset_status_on_end:
                    status_manager.set_idle()
                return False
            if not block_interrupt and playback_interrupt.is_set():
                if reset_status_on_end:
                    status_manager.set_idle()
                return False
            time.sleep(0.1)
        debug_log("wait_for_cos: Debounce successful, proceeding to play")

    try:
        playing_name = os.path.splitext(os.path.basename(filename))[0]
        if set_status_on_play and reset_status_on_end:
            status_manager.set_status("Playing", playing_name)
        proc = subprocess.Popen(
            ['aplay', '-D', SOUND_DEVICE, filename],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        debug_log("Exception starting aplay:", e)
        if reset_status_on_end:
            status_manager.set_idle()
        return False
    try:
        while proc.poll() is None:
            if playback_token is not None and playback_token != current_playback_token:
                proc.terminate()
                break
            if not block_interrupt and playback_interrupt.is_set():
                proc.terminate()
                break
            if interrupt_on_cos and is_cos_active():
                debug_log("COS became ACTIVE, interrupting playback")
                proc.terminate()
                time.sleep(0.1)
                if proc.poll() is None:
                    proc.kill()
                if reset_status_on_end:
                    status_manager.set_idle()
                return True
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if reset_status_on_end:
            status_manager.set_idle()
    return False

def get_base_type_and_info(base):
    for bases, ends, intervals, typ in [
        (random_bases, random_ends, random_intervals, 'Random'),
        (rotation_bases, rotation_ends, rotation_times, 'Rotation'),
        (sudo_bases, sudo_ends, sudo_intervals, 'SudoRandom')
    ]:
        if base in bases:
            idx = bases.index(base)
            end = ends[idx]
            interval = intervals[idx] * 60
            return typ, end, interval
    return None, None, None

def auto_alternate_series(command, interval_per_base=60):
    # interval_per_base should match the config for [Rotation] time in seconds
    bases, _, is_alt = parse_alternate_series(command)
    if not is_alt or not bases:
        debug_log("Not a valid alternate series command.")
        return

    key = tuple(sorted(bases))
    pointer = 0

    while True:
        current_base = bases[pointer]
        command_queue.put(command)
        debug_log(f"Played base {current_base}, waiting {interval_per_base} seconds before next base...")
        time.sleep(interval_per_base)
        pointer = (pointer + 1) % len(bases)

def get_next_base_file(base_code):
    typ, end, interval = get_base_type_and_info(base_code)
    if typ == "Rotation":
        available_tracks = []
        for track_num in range(base_code + 1, end + 1):
            for f in os.listdir(SOUND_DIRECTORY):
                if match_code_file(f, f"{track_num:04d}", SOUND_FILE_EXTENSION):
                    available_tracks.append((track_num, f))
                    break
        if not available_tracks:
            return None
        available_nums = [num for num, _ in available_tracks]
        current_num = rotation_current_track.get(base_code, available_nums[0])
        if current_num not in available_nums:
            current_num = available_nums[0]
        idx = available_nums.index(current_num)
        now = time.time()
        last_played = rotation_last_played.get(base_code, 0)
        if last_played == 0:
            next_idx = idx
            rotation_last_played[base_code] = now
        elif now - last_played >= interval:
            next_idx = (idx + 1) % len(available_nums)
            rotation_last_played[base_code] = now
        else:
            next_idx = idx
        rotation_current_track[base_code] = available_nums[next_idx]
        return os.path.join(SOUND_DIRECTORY, available_tracks[next_idx][1])
    elif typ == "Random":
        matching_files = find_matching_files(base_code, end)
        if not matching_files:
            return None
        last_played = random_last_played.get(base_code, 0)
        current_track = random_current_track.get(base_code, None)
        now = time.time()
        if now - last_played >= interval or current_track not in matching_files:
            chosen = random.choice(matching_files)
            random_current_track[base_code] = chosen
            random_last_played[base_code] = now
            return chosen
        else:
            return current_track
    elif typ == "SudoRandom":
        matching_files = find_matching_files(base_code, end)
        if not matching_files:
            return None
        played_in_cycle = sudo_random_played_in_cycle.get(base_code, set())
        if not isinstance(played_in_cycle, set):
            played_in_cycle = set(played_in_cycle)
        unused_tracks = [t for t in matching_files if t not in played_in_cycle]
        if not unused_tracks:
            played_in_cycle = set()
            unused_tracks = matching_files[:]
        chosen = random.choice(unused_tracks)
        sudo_random_interval_track[base_code] = chosen
        sudo_random_last_interval[base_code] = time.time()
        played_in_cycle.add(chosen)
        sudo_random_played_in_cycle[base_code] = played_in_cycle
        return chosen
    else:
        #Match dash-suffixed files for direct (non-section) case
        files = os.listdir(SOUND_DIRECTORY)
        matches = [f for f in files if match_code_file(f, f"P{base_code}", SOUND_FILE_EXTENSION)]
        if matches:
            return os.path.join(SOUND_DIRECTORY, matches[0])
        return None

def play_any_section_by_type(base, end, interval, typ, interruptible, repeat, pausing, wait_for_cos=False):
    if typ == "Random":
        play_randomized_section(base, end, interval, random_last_played, random_current_track, interruptible, wait_for_cos=wait_for_cos)
    elif typ == "Rotation":
        play_rotating_section(base, end, interval, rotation_last_played, rotation_current_track, interruptible, wait_for_cos=wait_for_cos)
    elif typ == "SudoRandom":
        play_sudo_random_section(base, end, interval, sudo_random_last_interval, sudo_random_interval_track, sudo_random_played_in_cycle, interruptible, repeat, pausing, wait_for_cos=wait_for_cos)
    else:
        play_direct_track(f"{base:04d}", interruptible, repeat, pausing, wait_for_cos=wait_for_cos)

def format_currently_playing(track_num, title, base_type, base_num):
    if title:
        return f"{track_num}-{title} from {base_type} {base_num}"
    else:
        return f"{track_num} from {base_type} {base_num}"

def play_rotating_section(
    base,
    end,
    interval,
    last_played_dict,
    current_track_dict,
    interruptible=False,
    pausing=False,
    repeat=False,
    wait_for_cos=False
):
    try:
        import os
        global ctone_override_expire
        current_time = time.time()
        last_played = last_played_dict.get(base, 0)

        available_tracks = []
        for track_num in range(base + 1, end + 1):
            for f in os.listdir(SOUND_DIRECTORY):
                if match_code_file(f, f"{track_num:04d}", SOUND_FILE_EXTENSION):
                    available_tracks.append((track_num, f))
                    break

        if not available_tracks:
            status_manager.set_idle()
            return

        current_file = current_track_dict.get(base)
        current_track_num = None
        if current_file:
            try:
                current_track_num = int(os.path.basename(current_file).split("-")[0].split(".")[0])
            except:
                current_track_num = None

        current_idx = 0
        if current_track_num:
            for idx, (num, _) in enumerate(available_tracks):
                if num == current_track_num:
                    current_idx = idx
                    break

        if last_played == 0:
            next_idx = current_idx
            last_played_dict[base] = current_time
        elif current_time - last_played >= interval:
            next_idx = (current_idx + 1) % len(available_tracks)
            last_played_dict[base] = current_time
        else:
            next_idx = current_idx

        next_track_num, next_file = available_tracks[next_idx]
        current_track_dict[base] = next_file
        next_track = os.path.join(SOUND_DIRECTORY, next_file)

        filebase = os.path.splitext(os.path.basename(next_track))[0]

        # --- CTONE OVERRIDE CHECK ---
        wx_alerts = config.getboolean('WX', 'alerts', fallback=False)
        ctone = config.get('WX', 'ctone', fallback='').strip()
        now = time.time()
        debug_log(f"[CTONE PATCH] ROTATING WX_ALERTS: {wx_alerts}, CTONE: '{ctone}', OVERRIDE_EXPIRE: {ctone_override_expire}, NOW: {now}")
        if wx_alerts and ctone and ctone.isdigit() and len(ctone) == 4 and now < ctone_override_expire:
            import re
            m = re.match(r'^(\d{4})([A-Z]*)-CT\b.*', filebase, re.IGNORECASE)
            if m:
                suffix = m.group(2)
                new_code_str = ctone + suffix
                log_recent(f"CT Override {filebase} -> {new_code_str}.")
                debug_log(f"[CTONE PATCH] play_rotating_section: OVERRIDE {filebase} -> {new_code_str}")
                play_direct_track(new_code_str, interruptible, pausing, repeat, wait_for_cos)
                rotation_active[base] = False
                return  # <---- CRUCIAL

        track_filename = os.path.basename(next_file)
        track_num = track_filename.split("-")[0].split(".")[0]
        title = track_filename.split("-", 1)[1].rsplit(".", 1)[0] if "-" in track_filename else ""

        def format_currently_playing(track_num, title, base_type, base_num):
            if title:
                return f"{track_num}-{title} from {base_type} {base_num}"
            else:
                return f"{track_num} from {base_type} {base_num}"

        currently_playing_str = format_currently_playing(track_num, title, "Rotating Base", base)

        play_sound(
            next_track,
            interruptible=interruptible,
            pausing=pausing,
            repeating=repeat,
            wait_for_cos=wait_for_cos,
            display_name=currently_playing_str
        )
        rotation_active[base] = False
    except Exception:
        log_exception("play_rotating_section")
        rotation_active[base] = False
        status_manager.set_idle()

def play_randomized_section(
    base,
    end,
    interval,
    last_played_dict,
    current_track_dict,
    interruptible=False,
    pausing=False,
    repeating=False,
    wait_for_cos=False
):
    try:
        global ctone_override_expire
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

        import os
        filebase = os.path.splitext(os.path.basename(new_track))[0]

        # --- CTONE OVERRIDE CHECK ---
        wx_alerts = config.getboolean('WX', 'alerts', fallback=False)
        ctone = config.get('WX', 'ctone', fallback='').strip()
        now = time.time()
        debug_log(f"[CTONE PATCH] RANDOM WX_ALERTS: {wx_alerts}, CTONE: '{ctone}', OVERRIDE_EXPIRE: {ctone_override_expire}, NOW: {now}")
        if wx_alerts and ctone and ctone.isdigit() and len(ctone) == 4 and now < ctone_override_expire:
            import re
            m = re.match(r'^(\d{4})([A-Z]*)-CT\b.*', filebase, re.IGNORECASE)
            if m:
                suffix = m.group(2)
                new_code_str = ctone + suffix
                log_recent(f"CT Override {filebase} -> {new_code_str}.")
                debug_log(f"[CTONE PATCH] play_randomized_section: OVERRIDE {filebase} -> {new_code_str}")
                play_direct_track(new_code_str, interruptible, pausing, repeating, wait_for_cos)
                return  # <---- CRUCIAL

        track_filename = os.path.basename(new_track)
        track_num = track_filename.split("-")[0].split(".")[0]
        title = track_filename.split("-", 1)[1].rsplit(".", 1)[0] if "-" in track_filename else ""

        def format_currently_playing(track_num, title, base_type, base_num):
            if title:
                return f"{track_num}-{title} from {base_type} {base_num}"
            else:
                return f"{track_num} from {base_type} {base_num}"

        currently_playing_str = format_currently_playing(track_num, title, "Random Base", base)

        play_sound(
            new_track,
            interruptible=interruptible,
            pausing=pausing,
            repeating=repeating,
            wait_for_cos=wait_for_cos,
            display_name=currently_playing_str
        )
    except Exception:
        log_exception("play_randomized_section")

def play_sudo_random_section(
    base,
    end,
    interval,
    last_interval_dict,
    interval_track_dict,
    played_in_cycle_dict,
    interruptible=False,
    pausing=False,
    repeat=False,
    wait_for_cos=False
):
    global sudo_random_last_file
    global ctone_override_expire
    current_time = time.time()
    matching_files = find_matching_files(base, end)
    if not matching_files:
        return
    last_interval = last_interval_dict.get(base, 0)
    current_track = interval_track_dict.get(base)
    played_in_cycle = played_in_cycle_dict.get(base)
    if played_in_cycle is None:
        played_in_cycle = set()
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

    import os
    filebase = os.path.splitext(os.path.basename(file_to_play))[0]

    # --- CTONE OVERRIDE CHECK ---
    wx_alerts = config.getboolean('WX', 'alerts', fallback=False)
    ctone = config.get('WX', 'ctone', fallback='').strip()
    now = time.time()
    debug_log(f"[CTONE PATCH] SUDORANDOM WX_ALERTS: {wx_alerts}, CTONE: '{ctone}', OVERRIDE_EXPIRE: {ctone_override_expire}, NOW: {now}")
    if wx_alerts and ctone and ctone.isdigit() and len(ctone) == 4 and now < ctone_override_expire:
        import re
        m = re.match(r'^(\d{4})([A-Z]*)-CT\b.*', filebase, re.IGNORECASE)
        if m:
            suffix = m.group(2)
            new_code_str = ctone + suffix
            log_recent(f"CT Override {filebase} -> {new_code_str}.")
            debug_log(f"[CTONE PATCH] play_sudo_random_section: OVERRIDE {filebase} -> {new_code_str}")
            play_direct_track(new_code_str, interruptible, pausing, repeat, wait_for_cos)
            return  # <---- CRUCIAL

    track_filename = os.path.basename(file_to_play)
    track_num = track_filename.split("-")[0].split(".")[0]
    title = track_filename.split("-", 1)[1].rsplit(".", 1)[0] if "-" in track_filename else ""

    def format_currently_playing(track_num, title, base_type, base_num):
        if title:
            return f"{track_num}-{title} from {base_type} {base_num}"
        else:
            return f"{track_num} from {base_type} {base_num}"

    currently_playing_str = format_currently_playing(track_num, title, "SudoRandom Base", base)
    play_sound(
        file_to_play,
        interruptible=interruptible,
        pausing=pausing,
        repeating=repeat,
        wait_for_cos=wait_for_cos,
        display_name=currently_playing_str
    )

def play_direct_track(code_str, interruptible=False, pausing=False, repeat=False, wait_for_cos=False):
    """
    Play a track directly by its code string, optionally applying C-tone WX alert override.
    """
    debug_log(f"play_direct_track: code_str={code_str}, interruptible={interruptible}, pausing={pausing}, repeat={repeat}, wait_for_cos={wait_for_cos}")
    global ctone_override_expire

    # --- C-tone WX Alert Override Logic ---
    wx_alerts = config.getboolean('WX', 'alerts', fallback=False)
    ctone = config.get('WX', 'ctone', fallback='').strip()
    now = time.time()
    import re
    debug_log(f"[CTONE PATCH] DIRECT WX_ALERTS: {wx_alerts}, CTONE: '{ctone}', OVERRIDE_EXPIRE: {ctone_override_expire}, NOW: {now}")
    if wx_alerts and ctone and ctone.isdigit() and len(ctone) == 4 and now < ctone_override_expire:
        m = re.match(r'^(\d{4})([A-Z]*)-CT\b.*', code_str, re.IGNORECASE)
        if m:
            suffix = m.group(2)
            new_code_str = ctone + suffix
            log_recent(f"CT Override {filebase} -> {new_code_str}.")
            debug_log(f"[CTONE PATCH] play_direct_track: OVERRIDE {code_str} -> {new_code_str}")
            code_str = new_code_str

    # Now resolve the code_str to a filename and play it
    import os
    matches = [
        f for f in os.listdir(SOUND_DIRECTORY)
        if match_code_file(f, code_str, SOUND_FILE_EXTENSION)
    ]
    if not matches:
        debug_log(f"File {code_str}{SOUND_FILE_EXTENSION} not found in play_direct_track.")
        status_manager.set_idle()
        return False
    filename = os.path.join(SOUND_DIRECTORY, matches[0])

    debug_log(
        f"play_direct_track: resolved filename={filename}, interruptible={interruptible}, pausing={pausing}, repeat={repeat}, wait_for_cos={wait_for_cos}"
    )
    # Pass through to play_sound or play_single_wav or your preferred playback mechanism
    play_sound(
        filename,
        interruptible=interruptible,
        pausing=pausing,
        repeating=repeat,
        wait_for_cos=wait_for_cos,
        display_name=os.path.splitext(os.path.basename(filename))[0]
    )
    return True     

def play_interrupt_to_another(base_filename, code2, playback_token=None):
    import os
    global currently_playing, currently_playing_info, currently_playing_info_timestamp
    global playback_status, playback_interrupt, current_playback_token
    debug_log(f"play_interrupt_to_another: base_filename={base_filename}, code2={code2}")

    base_file = os.path.basename(base_filename)
    base_file_noext = os.path.splitext(base_file)[0]
    code2_name = os.path.splitext(os.path.basename(str(code2)))[0]

    section_context = detect_section_context(base_filename)
    if section_context:
        playing_with_context = f"{base_file_noext} {section_context}"
    else:
        playing_with_context = base_file_noext

    interrupted = False
    try:
        set_remote_busy(True)
        if is_cos_active():
            status_manager.set_status(f"Playing {code2_name} (COS active at start)",
                                     code2_name, f"Playing {code2_name} (COS active at start)")
            play_single_wav(code2, block_interrupt=True, playback_token=playback_token)
            log_recent(f"Interrupt: COS active at start, played {code2_name} directly")
        else:
            status_manager.set_status(f"Playing {base_file_noext}, will interrupt to {code2_name} on COS",
                                     playing_with_context,
                                     f"Playing {base_file_noext} (will interrupt to {code2_name} if COS)")
            interrupted = play_single_wav(
                base_filename,
                interrupt_on_cos=True,
                playback_token=playback_token,
                set_status_on_play=False  # <--- Prevent overwrite!
            )
            if interrupted:
                status_manager.set_status(f"Playing {code2_name} (interrupted from {base_file_noext})",
                                         code2_name, f"Interrupted {base_file_noext}, now playing {code2_name}")
                play_single_wav(code2, block_interrupt=True, playback_token=playback_token)
                log_recent(f"Interrupt: {base_file_noext} interrupted by COS, switched to {code2_name}")
            else:
                log_recent(f"Interrupt: {base_file_noext} played without COS, did not switch to {code2_name}")
    finally:
        set_remote_busy(False)
        status_manager.set_idle()

def find_matching_files(base, end):
    files = []
    try:
        for track_num in range(base, end + 1):
            matching = [f for f in os.listdir(SOUND_DIRECTORY)
                        if match_code_file(f, f"{track_num:04d}", SOUND_FILE_EXTENSION)]
            files.extend([os.path.join(SOUND_DIRECTORY, f) for f in matching])
    except Exception:
        log_exception("find_matching_files")
    return files

def parse_echo_command(command):
    """
    Parses an echo test command in the format Re9999
    Returns the track number or None if not a valid echo command
    """
    import re
    command = command.strip().upper()
    match = re.match(r'^RE(\d{4})$', command)
    if match:
        track_num = int(match.group(1))
        return track_num
    return None

def echo_test(track_num, playback_token=None):
    """
    Perform an Echo Test, recording audio when COS is active and playing it back afterward.
    
    Args:
        track_num (int): The track number to create (e.g., 9999)
        playback_token: Token for playback interruption tracking
    """
    import os
    import time
    import subprocess
    global currently_playing, currently_playing_info, currently_playing_info_timestamp, playback_status
    global echo_test_active, echo_test_track
    
    try:
        echo_test_active = True
        echo_test_track = track_num
        
        # Format the target track number and filename
        track_str = f"{track_num:04d}"
        output_filename = os.path.join(SOUND_DIRECTORY, f"{track_str}.wav")
        echo_start_filename = os.path.join(SOUND_DIRECTORY, "echo-start.wav")
        echo_timeout_filename = os.path.join(SOUND_DIRECTORY, "echo-to.wav")
        echo_end_filename = os.path.join(SOUND_DIRECTORY, "echo-end.wav")
        
        debug_log(f"ECHO TEST FUNCTION STARTED with track_num={track_num}")
        debug_log(f"ECHO TEST: output_filename={output_filename}")
        debug_log(f"ECHO TEST: echo_start_filename={echo_start_filename}")
        debug_log(f"ECHO TEST: echo_timeout_filename={echo_timeout_filename}")
        debug_log(f"ECHO TEST: echo_end_filename={echo_end_filename}")
        
        # Set REMOTE_BUSY_PIN active for the entire Echo Test process
        set_remote_busy(True)
        
        try:
            # Update status
            status_manager.set_echo_test(track_num, "Waiting for COS to be inactive")
            
            # 1. Wait until COS is inactive
            debug_log("ECHO TEST: Waiting for COS to be inactive")
            cos_state = is_cos_active()
            debug_log(f"ECHO TEST: Current COS state: {cos_state}")
            
            while is_cos_active():
                time.sleep(0.1)
            
            debug_log("ECHO TEST: COS is inactive, proceeding")
            
            # 2. Play the echo-start.wav file if it exists
            if os.path.exists(echo_start_filename):
                status_manager.set_echo_test(track_num, "Playing intro prompt")
                
                try:
                    proc = subprocess.Popen(
                        ['aplay', '-D', SOUND_DEVICE, echo_start_filename],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    proc.wait()
                    debug_log("ECHO TEST: Played echo-start.wav")
                except Exception as e:
                    debug_log(f"Exception playing echo-start.wav: {e}")
            
            # 3. Wait for COS to become active and then record (with 5-second timeout)
            status_manager.set_echo_test(track_num, "Waiting for COS to begin recording")
            
            debug_log("ECHO TEST: Waiting for COS to become active (5s timeout)")
            
            # Wait for COS to become active with 5-second timeout
            cos_wait_start = time.time()
            cos_active = False
            
            while time.time() - cos_wait_start < 5:  # 5-second timeout
                if is_cos_active():
                    cos_active = True
                    break
                time.sleep(0.1)
            
            # If COS didn't become active within 5 seconds, play timeout message
            if not cos_active:
                debug_log("ECHO TEST: Timeout waiting for COS to become active")
                log_recent(f"Echo Test: Timed out waiting for input for track {track_str}")
                
                # Play the timeout message if it exists
                if os.path.exists(echo_timeout_filename):
                    status_manager.set_echo_test(track_num, "Playing timeout message")
                    
                    try:
                        debug_log("ECHO TEST: Playing echo-to.wav timeout message")
                        proc = subprocess.Popen(
                            ['aplay', '-D', SOUND_DEVICE, echo_timeout_filename],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        proc.wait()
                    except Exception as e:
                        debug_log(f"Exception playing echo-to.wav: {e}")
                
                # Clean up and exit
                echo_test_active = False
                echo_test_track = None
                return
            
            debug_log("ECHO TEST: COS is active, starting recording")
            
            # Start recording
            status_manager.set_echo_test(track_num, "Recording audio")
            
            record_proc = subprocess.Popen(
                ['arecord', '-D', SOUND_DEVICE, '-f', 'S16_LE', '-r', '44100', '-c', '1', output_filename],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # 4. Monitor COS to determine when to stop recording (with 1-minute maximum)
            cos_inactive_time = None
            recording_start_time = time.time()
            max_recording_time = 60  # 1 minute limit
            
            debug_log("ECHO TEST: Recording in progress, monitoring COS (max 1 minute)")
            
            while True:
                # Check for maximum recording time
                if time.time() - recording_start_time >= max_recording_time:
                    debug_log("ECHO TEST: Maximum recording time reached (1 minute)")
                    break
                    
                # Check COS state
                if is_cos_active():
                    cos_inactive_time = None
                else:
                    if cos_inactive_time is None:
                        debug_log("ECHO TEST: COS became inactive, starting debounce timer")
                        cos_inactive_time = time.time()
                    elif time.time() - cos_inactive_time >= COS_DEBOUNCE_TIME:
                        # COS has been inactive for the debounce period, stop recording
                        debug_log(f"ECHO TEST: COS inactive for {COS_DEBOUNCE_TIME}s, stopping recording")
                        break
                
                time.sleep(0.1)
            
            # Stop the recording
            debug_log("ECHO TEST: Terminating recording process")
            record_proc.terminate()
            time.sleep(0.5)  # Give the process time to cleanly terminate
            if record_proc.poll() is None:
                debug_log("ECHO TEST: Killing recording process")
                record_proc.kill()
            
            # Check if the file was created and is valid
            if not os.path.exists(output_filename):
                debug_log("ECHO TEST: Output file does not exist")
                log_recent(f"Echo Test: Recording failed, no file created for track {track_str}")
                echo_test_active = False
                echo_test_track = None
                return
            
            file_size = os.path.getsize(output_filename)
            debug_log(f"ECHO TEST: Recording completed, file size: {file_size} bytes")
            
            if file_size < 1000:  # Minimum valid file size check
                debug_log("ECHO TEST: File too small, likely invalid")
                log_recent(f"Echo Test: Recording failed, file too small for track {track_str}")
                echo_test_active = False
                echo_test_track = None
                return
            
            # Set full permissions (read, write, execute) for all users
            try:
                os.chmod(output_filename, 0o777)  # rwxrwxrwx permissions
                debug_log(f"ECHO TEST: Set full permissions (777) on {output_filename}")
            except Exception as e:
                debug_log(f"ECHO TEST: Failed to set file permissions: {e}")
            
            # 5. Play back the recording
            debug_log("ECHO TEST: Starting playback of recording")
            status_manager.set_echo_test(track_num, "Playing back recording")
            
            playback_proc = subprocess.Popen(
                ['aplay', '-D', SOUND_DEVICE, output_filename],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            playback_proc.wait()
            
            debug_log("ECHO TEST: Playback completed")
            log_recent(f"Echo Test: Successfully recorded and played back track {track_str}")
            
            # 6. Play the echo-end.wav file if it exists
            if os.path.exists(echo_end_filename):
                status_manager.set_echo_test(track_num, "Playing end prompt")
                
                try:
                    debug_log("ECHO TEST: Playing echo-end.wav")
                    proc = subprocess.Popen(
                        ['aplay', '-D', SOUND_DEVICE, echo_end_filename],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    proc.wait()
                    debug_log("ECHO TEST: Played echo-end.wav")
                except Exception as e:
                    debug_log(f"Exception playing echo-end.wav: {e}")
            
        except Exception as e:
            debug_log(f"Exception in echo test: {e}")
            log_exception("echo_test")
        finally:
            # Now release REMOTE_BUSY_PIN after everything is done
            set_remote_busy(False)
            status_manager.set_idle()
            echo_test_active = False
            echo_test_track = None
            debug_log("ECHO TEST: Function completed")
    except Exception as e:
        # Make sure we release the REMOTE_BUSY_PIN even on outer exception
        set_remote_busy(False)
        echo_test_active = False
        echo_test_track = None
        debug_log(f"Exception in echo_test outer try: {e}")
        log_exception("echo_test_outer")

def serial_read_loop():
    global serial_history, dtmf_buffer, dtmf_lock
    dtmf_pattern = re.compile(r"([123])D([0-9A-D\*#])", re.IGNORECASE)

    serial_port = None
    reconnect_delay = 5
    last_connection_attempt = 0

    line_buffer = ""
    last_data_time = time.time()

    while True:
        current_time = time.time()

        # Attempt to connect if not connected
        if serial_port is None and current_time - last_connection_attempt > reconnect_delay:
            try:
                debug_log(f"Attempting to connect to serial port {SERIAL_PORT}...")
                serial_port = serial.Serial(
                    port=SERIAL_PORT,
                    baudrate=SERIAL_BAUDRATE,
                    timeout=SERIAL_TIMEOUT
                )
                serial_port.reset_input_buffer()
                debug_log("Serial connection established successfully")
                reconnect_delay = 5
            except Exception as e:
                debug_log(f"Serial connection failed: {e}")
                serial_port = None
                debug_log(f"Will attempt reconnection in {reconnect_delay} seconds...")
                reconnect_delay = min(reconnect_delay * 1.5, 60)
            last_connection_attempt = current_time

        # Read and buffer serial data
        try:
            if serial_port and serial_port.is_open:
                while serial_port.in_waiting > 0:
                    data = serial_port.read(serial_port.in_waiting)
                    debug_log(f"Raw serial bytes: {data!r}")
                    decoded = data.decode('ascii', errors='ignore')
                    line_buffer += decoded
                    debug_log(f"Line buffer: {line_buffer!r}")
                    last_data_time = current_time

                # Process all complete lines
                while '\n' in line_buffer:
                    line, line_buffer = line_buffer.split('\n', 1)
                    cleaned_line = line.strip()
                    debug_log(f"Got serial line: {cleaned_line!r}")

                    # --- TOT/TOP Time Out Timer logic ---
                    if cleaned_line == "TOT":
                        debug_log("TOT command received.")
                        handle_tot_start()
                                            
                    if cleaned_line:
                        m = dtmf_pattern.match(cleaned_line)
                        debug_log(f"DTMF match: {m is not None}")
                        debug_log(f"is_cos_active: {is_cos_active()}")
                        if m and is_cos_active():
                            port, digit = m.group(1), m.group(2)
                            with dtmf_lock:
                                dtmf_buffer.setdefault(port, []).append(str(digit))
                            debug_log(f"DTMF buffered: {dtmf_buffer}")
                        serial_history.insert(0, {
                            "cmd": cleaned_line,
                            "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            "src": "Serial"
                        })
                        if len(serial_history) > 10:
                            serial_history.pop()
                        command_queue.put(cleaned_line)
                        debug_log(f"[SERIAL LOOP] Queued command: {cleaned_line!r}")
                    last_data_time = current_time

                # Timeout: clear junk if buffer is old and incomplete
                LINE_TIMEOUT = get_config_value("Serial", "line_timeout", 3.0, float)
                if line_buffer and (current_time - last_data_time > LINE_TIMEOUT):
                    debug_log(f"[SERIAL LOOP] Incomplete/junk data in buffer for >{LINE_TIMEOUT}s, clearing: {repr(line_buffer)}")
                    line_buffer = ""

        except serial.SerialException as e:
            debug_log(f"Serial device error: {e}")
            debug_log("Serial device disconnected, will attempt to reconnect")
            try:
                if serial_port is not None and serial_port.is_open:
                    serial_port.close()
            except:
                pass
            serial_port = None

        except Exception as e:
            debug_log(f"Unexpected error in serial read loop: {e}")

        time.sleep(0.05)

def play_code(code_str, interruptible=False, pausing=False, repeating=False, wait_for_cos=False):
    debug_log(f"play_code: code_str={code_str}")
    files = os.listdir(SOUND_DIRECTORY)
    debug_log(f"play_code: Directory listing: {files}")
    matches = [f for f in files if match_code_file(f, code_str, SOUND_FILE_EXTENSION)]
    debug_log(f"play_code: Matching files for code_str={code_str}: {matches}")
    if matches:
        filename = os.path.join(SOUND_DIRECTORY, matches[0])
        debug_log(f"play_code: Starting playback for {filename}")
        # Directly call play_sound, no new thread, no launch_playback_thread
        play_sound(
            filename=filename,
            interruptible=interruptible,
            pausing=pausing,
            repeating=repeating,
            wait_for_cos=wait_for_cos
        )
    else:
        debug_log(f"play_code: No sound file found for code_str={code_str}")

def parse_serial_command(command):
    """
    Accepts:
      - Pxxxx
      - PxxxxI (and combinations like PxxxxIM)
      - PxxxxP (and combinations like PxxxxPM)
      - PxxxxR (and combinations like PxxxxRM)
      - PxxxxiYYYY (interrupt-to-another), ignoring any suffix after second code
    """
    # Strip whitespace but don't convert to uppercase at all
    command = command.strip()
    
    # First check for lowercase 'i' interrupt-to-another format
    m = re.match(r'^[Pp](\d{4})i(\d{4})', command)
    if m:
        return (m.group(1), "i", m.group(2))
    
    # Check for valid suffix combinations - match only uppercase characters
    m = re.match(r'^[Pp](\d{4})([IPRWM]+)$', command)
    if m:
        return (m.group(1), m.group(2), None)
        
    # Basic command with no mode
    m = re.match(r'^[Pp](\d{4})$', command)
    if m:
        return (m.group(1), "", None)
        
    return (None, None, None)

def process_serial_commands():
    """
    Continuously scan the serial_buffer for valid command patterns, including standalone A1, W1, and W2 commands,
    and join-series (J) and alternate-series (A) commands.
    """
    global serial_buffer

    while True:
        try:
            # Skip processing if buffer is empty
            if not serial_buffer:
                time.sleep(0.1)
                continue

            # Special check just for RE commands with extra debugging
            if "RE" in serial_buffer.upper():
                debug_log(f"FOUND RE PATTERN IN BUFFER: '{serial_buffer}'")

                # Very simple pattern matching for RE commands
                match = re.search(r'RE\d{4}', serial_buffer.upper())
                if match:
                    echo_cmd = match.group(0)
                    debug_log(f"MATCHED ECHO COMMAND: '{echo_cmd}'")

                    # Extract the track number
                    track_str = echo_cmd[2:]  # Remove the "RE" prefix
                    try:
                        track_num = int(track_str)
                        debug_log(f"EXTRACTED TRACK NUMBER: {track_num}")

                        # Queue the echo test command as a string ("RE1234")
                        debug_log("QUEUEING ECHO TEST COMMAND")
                        command_queue.put(f"RE{track_num:04d}")

                        # Update history
                        serial_history.insert(0, {
                            "cmd": f"Echo Test: {track_num:04d}",
                            "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            "src": "Serial"
                        })
                        log_recent(f"Echo Test initiated for track {track_num:04d}")

                        # Remove the command from the buffer
                        index = serial_buffer.upper().find(echo_cmd)
                        if index != -1:
                            serial_buffer = serial_buffer[:index] + serial_buffer[index+len(echo_cmd):]
                            serial_buffer = serial_buffer.lstrip()
                            debug_log(f"BUFFER AFTER ECHO COMMAND REMOVAL: '{serial_buffer}'")
                    except ValueError:
                        debug_log(f"Invalid track number format: {track_str}")
                    continue

            # Standalone A1 command (case-insensitive, surrounded by non-word chars or start/end)
            match_a1 = re.search(r'\bA1\b', serial_buffer.upper())
            if match_a1:
                debug_log(f"Processing A1 command")
                command_queue.put("A1")
                # Remove A1 from buffer
                index = serial_buffer.upper().find("A1")
                if index != -1:
                    serial_buffer = serial_buffer[:index] + serial_buffer[index+2:]
                serial_buffer = serial_buffer.lstrip()
                continue

            # Standalone W1 command (case-insensitive, surrounded by non-word chars or start/end)
            match_w1 = re.search(r'\bW1\b', serial_buffer.upper())
            if match_w1:
                debug_log(f"Processing W1 command")
                command_queue.put("W1")
                # Remove W1 from buffer
                index = serial_buffer.upper().find("W1")
                if index != -1:
                    serial_buffer = serial_buffer[:index] + serial_buffer[index+2:]
                serial_buffer = serial_buffer.lstrip()
                continue

            # Standalone W2 command (case-insensitive, surrounded by non-word chars or start/end)
            match_w2 = re.search(r'\bW2\b', serial_buffer.upper())
            if match_w2:
                debug_log(f"Processing W2 command")
                command_queue.put("W2")
                # Remove W2 from buffer
                index = serial_buffer.upper().find("W2")
                if index != -1:
                    serial_buffer = serial_buffer[:index] + serial_buffer[index+2:]
                serial_buffer = serial_buffer.lstrip()
                continue
            
            # Standalone W3 command (case-insensitive, surrounded by non-word chars or start/end)
            match_w3 = re.search(r'\bW3\b', serial_buffer.upper())
            if match_w3:
                debug_log(f"Processing W3 command")
                command_queue.put("W3")
                # Remove W3 from buffer
                index = serial_buffer.upper().find("W3")
                if index != -1:
                    serial_buffer = serial_buffer[:index] + serial_buffer[index+2:]
            serial_buffer = serial_buffer.lstrip()
            continue

            # --- Join series (J) and alternate series (A) pattern matching ---
            # Join series: e.g. P1001JR2002IM or P1001J2002J3003M
            # Alternate series: e.g. P1001A2002A3003M
            # Both patterns must start with P, have at least one J or A, and 4 digits per base.
            join_pattern = r'P\d{4}(J\d{4}[A-Z]*)+([A-Z]*)'
            alt_pattern = r'P\d{4}(A\d{4})+([A-Z]*)'

            match_join = re.search(join_pattern, serial_buffer)
            if match_join:
                command = match_join.group(0)
                debug_log(f"Processing join-series command: '{command}'")
                command_queue.put(command)
                index = serial_buffer.find(command)
                if index != -1:
                    serial_buffer = serial_buffer[:index] + serial_buffer[index+len(command):]
                serial_buffer = serial_buffer.lstrip()
                continue

            match_alt = re.search(alt_pattern, serial_buffer)
            if match_alt:
                command = match_alt.group(0)
                debug_log(f"Processing alternate-series command: '{command}'")
                command_queue.put(command)
                index = serial_buffer.find(command)
                if index != -1:
                    serial_buffer = serial_buffer[:index] + serial_buffer[index+len(command):]
                serial_buffer = serial_buffer.lstrip()
                continue

            # Accepts Pxxxx, PxxxxI, PxxxxR, PxxxxIM, etc., and COS-I (PxxxxIyyyy)
            pattern = r'P\d{4}(I\d{4}|i\d{4}|[IPRM]*)'
            match = re.search(pattern, serial_buffer.upper())
            if match:
                command = match.group(0)
                debug_log(f"Processing command: '{command}'")
                command_queue.put(command)
                index = serial_buffer.upper().find(command)
                if index != -1:
                    serial_buffer = serial_buffer[:index] + serial_buffer[index+len(command):]
                serial_buffer = serial_buffer.lstrip()

            # If we've processed everything but still have data in buffer
            # and no matches were found, clear some old data to prevent buildup
            if len(serial_buffer) > 1000:
                debug_log(f"Trimming excess data from serial buffer (length: {len(serial_buffer)})")
                serial_buffer = serial_buffer[-500:]  # Keep the last 500 chars

            time.sleep(0.1)
        except Exception as e:
            debug_log(f"Exception in process_serial_commands: {e}")
            log_exception("process_serial_commands")
            time.sleep(0.5)  # Longer sleep on error

def bg_write_state_and_webcmd_loop():
    while True:
        maybe_run_webcmd()
        write_state()
        time.sleep(0.25)

def bg_cos_state_update_loop():
    global cos_active, last_cos
    last_cos = None
    last_update = time.time()
    while True:
        try:
            cos_now = is_cos_active()
            if cos_now != last_cos:
                cos_active = cos_now
                last_cos = cos_now
            # Only update if COS is active
            if cos_active:
                now = time.time()
                # Only increment once per second
                if now - last_update >= 1:
                    debug_log("Increment block running")
                    update_cos_minutes()
                    last_update = now
        except Exception as e:
            print("Exception in bg_cos_state_update_loop:", e)
        time.sleep(0.05)

def should_allow_message_timer_play(message_mode, timer_value, last_played):
    """
    Returns True if playback should be allowed for message-timer-locked playback.
    - timer_value: in minutes (can be 0, 'N', or a number)
    - last_played: timestamp of last allowed message playback
    """
    if not message_mode or timer_value is None:
        return True
    if timer_value == 'N':
        return False
    if timer_value == 0:
        return True
    now = time.time()
    if last_played is not None and now - last_played < timer_value * 60:
        return False
    return True

def update_message_timer_state(last_played, interval):
    """
    DISABLED: Message timer state is now stored in memory only.
    This function is kept for compatibility but does nothing.
    Message timer values are updated through write_state().
    """
    # No longer write state to file - all state is in memory only
    global message_timer_last_played, message_timer_value
    message_timer_last_played = last_played
    message_timer_value = interval
        
def set_message_rate_limited():
    global rate_limited_set_time, rate_limited_timer

    status_manager.set_status("Message is Rate-Limited", "", "")
    rate_limited_set_time = time.time()

    # Cancel any previous timer
    if rate_limited_timer is not None:
        rate_limited_timer.cancel()

    # Start a new 5-second timer
    rate_limited_timer = threading.Timer(5, clear_rate_limited_status)
    rate_limited_timer.start()

def clear_rate_limited_status():
    global rate_limited_set_time, rate_limited_timer

    status_info = status_manager.get_status_info()
    if status_info['playback_status'] == "Message is Rate-Limited":
        status_manager.set_idle()
        rate_limited_set_time = None
    rate_limited_timer = None

def cancel_rate_limited_timer():
    global rate_limited_timer
    if rate_limited_timer is not None:
        rate_limited_timer.cancel()
        rate_limited_timer = None 

def run_script(script_num, playback_token=None):
    """
    Executes a script from the scripts directory based on the script number.
    
    Args:
        script_num (str): The script number/name to execute
        playback_token: Token for playback interruption tracking (not used but required)
    """
    import os
    import subprocess
    import time
    global currently_playing, currently_playing_info, currently_playing_info_timestamp, playback_status
    
    # Get the base DRX directory (one level up from SOUND_DIRECTORY)
    drx_dir = os.path.abspath(os.path.join(os.path.dirname(SOUND_DIRECTORY), ".."))
    scripts_dir = os.path.join(drx_dir, "scripts")
    
    # Log the paths for debugging
    debug_log(f"SCRIPT: SOUND_DIRECTORY={SOUND_DIRECTORY}")
    debug_log(f"SCRIPT: DRX directory calculated as={drx_dir}")
    debug_log(f"SCRIPT: Scripts directory calculated as={scripts_dir}")
    
    script_path = os.path.join(scripts_dir, script_num)
    debug_log(f"SCRIPT: Attempting to run script: {script_path}")
    
    try:
        # Check if script exists
        if not os.path.exists(script_path):
            debug_log(f"SCRIPT: Script does not exist: {script_path}")
            log_recent(f"Script execution failed: {script_num} - File not found")
            
            # Update status
            status_manager.set_status("Idle", "", f"Script {script_num} not found")
            return
        
        # Check if script is executable
        if not os.access(script_path, os.X_OK):
            debug_log(f"SCRIPT: Script is not executable: {script_path}")
            log_recent(f"Script execution failed: {script_num} - Not executable")
            
            # Update status
            status_manager.set_status("Idle", "", f"Script {script_num} not executable")
            return
            
        # Execute the script
        debug_log(f"SCRIPT: Executing script: {script_path}")
        status_manager.set_script_execution(script_num, "Executing")
        
        try:
            # Run the script and wait for it to complete
            proc = subprocess.Popen(
                [script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Wait for process to complete
            stdout, stderr = proc.communicate()
            exit_code = proc.returncode
            
            # Log the results
            debug_log(f"SCRIPT: Script {script_num} completed with exit code: {exit_code}")
            if stdout:
                debug_log(f"SCRIPT: Standard output: {stdout.decode('utf-8', errors='replace')}")
            if stderr:
                debug_log(f"SCRIPT: Standard error: {stderr.decode('utf-8', errors='replace')}")
                
            if exit_code == 0:
                log_recent(f"Script {script_num} executed successfully")
            else:
                log_recent(f"Script {script_num} completed with errors (exit code {exit_code})")
                
        except Exception as e:
            debug_log(f"SCRIPT: Error executing script {script_num}: {e}")
            log_exception("run_script_execution")
            log_recent(f"Script execution failed: {script_num} - Runtime error")
            
    except Exception as e:
        debug_log(f"SCRIPT: Exception in script execution: {e}")
        log_exception("run_script")
    finally:
        # Reset status after script completes
        status_manager.set_idle()

def archive_dtmf_log_if_new_month():
    """Archive the DTMF log if the first line is for a different month than now."""
    if not os.path.exists(DTMF_LOG_FILE):
        return
    try:
        with open(DTMF_LOG_FILE, "r") as f:
            first_line = f.readline()
        if first_line:
            file_month = first_line[:7]
            now_month = datetime.now().strftime("%Y-%m")
            if file_month != now_month:
                archive_name = datetime.now().strftime(DTMF_LOG_ARCHIVE_FMT)
                os.rename(DTMF_LOG_FILE, archive_name)
    except Exception:
        pass

def prepend_dtmf_log(lines):
    """Prepend new lines to the DTMF log (newest first)."""
    if os.path.exists(DTMF_LOG_FILE):
        with open(DTMF_LOG_FILE, "r") as f:
            prev = f.read()
    else:
        prev = ""
    with open(DTMF_LOG_FILE, "w") as f:
        f.write("\n".join(lines) + ("\n" if prev else "") + prev)
    try:
        os.chmod(DTMF_LOG_FILE, 0o666)
    except Exception:
        pass

def dtmf_cos_edge_monitor():
    last_cos = is_cos_active()
    while True:
        now_cos = is_cos_active()
        if last_cos and not now_cos:
            # COS just went inactive: flush DTMF buffer
            with dtmf_lock:
                if dtmf_buffer:
                    archive_dtmf_log_if_new_month()
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    entries = [
                        f"{now} Port {port}: {''.join(digits)}"
                        for port, digits in dtmf_buffer.items() if digits
                    ]
                    if entries:
                        prepend_dtmf_log(entries)
                    dtmf_buffer.clear()
        last_cos = now_cos
        time.sleep(0.02)

def write_state():
    """
    Store state in memory instead of writing to file.
    State is now accessible via /api/state endpoint.
    """
    global current_state_memory, cos_today_date
    now = time.time()

    # Keep track of last_played in memory only (no disk reads)
    # Use global variables to maintain state across calls
    global prev_currently_playing, last_played_memory
    if 'prev_currently_playing' not in globals():
        prev_currently_playing = ""
    if 'last_played_memory' not in globals():
        last_played_memory = ""

    # Decide what last_played should be for this write
    if (
        prev_currently_playing
        and prev_currently_playing.lower() != "idle"
        and prev_currently_playing != currently_playing
    ):
        last_played = prev_currently_playing
        last_played_memory = prev_currently_playing
    else:
        last_played = last_played_memory

    # Update previous state for next call
    prev_currently_playing = currently_playing

    # Random bases lines
    random_bases_lines = []
    for b, e, t in zip(random_bases, random_ends, random_intervals):
        last = random_last_played.get(b, 0)
        current = random_current_track.get(b, 'N/A')
        remaining = int(max(0, t*60 - (now - last)))
        if not current or str(current).upper() == "N/A":
            track_name = "N/A"
        else:
            track_name = os.path.splitext(os.path.basename(current))[0]
        random_bases_lines.append(
            f"Base {b} | End {e} Interval {t}: Track={track_name} Remaining={remaining}s"
        )

    # Rotation bases lines
    rotation_bases_lines = []
    for b, e, t in zip(rotation_bases, rotation_ends, rotation_times):
        last = rotation_last_played.get(b, 0)
        current_num = rotation_current_track.get(b, b+1)
        remaining = int(max(0, t*60 - (now - last)))
        rotation_bases_lines.append(
            f"Base {b} | End {e} Interval {t}: Track={current_num} Remaining={remaining}s"
        )

    # SudoRandom bases lines
    sudo_bases_lines = []
    for b, e, t in zip(sudo_bases, sudo_ends, sudo_intervals):
        last = sudo_random_last_interval.get(b, 0)
        current = sudo_random_interval_track.get(b, 'N/A')
        played = sudo_random_played_in_cycle.get(b, set())
        remaining = int(max(0, t*60 - (now - last)))
        if not current or str(current).upper() == "N/A":
            track_name = "N/A"
        else:
            track_name = os.path.splitext(os.path.basename(current))[0]
        sudo_bases_lines.append(
            f"Base {b} | End {e} Interval {t}: Track={track_name} Remaining={remaining}s PlayedInCycle={len(played)}"
        )

    # --- ALT SERIES STATE (NEW) ---
    alt_bases_lines = []
    for key, last_played_dict in alternate_series_last_played.items():
        bases = list(key)
        pointer = alternate_series_pointers.get(key, 0)
        track_pointers = alternate_series_track_pointers.get(key, {})
        for base in bases:
            typ, end, interval = get_base_type_and_info(base)
            last = last_played_dict.get(base, 0)
            remaining = int(max(0, interval - (now - last))) if interval else 0
            track_val = track_pointers.get(base, 1)
            if typ == 'Rotation':
                current_num = base + (track_val if isinstance(track_val, int) else 1)
                track_display = str(current_num)
            else:
                if track_val and isinstance(track_val, str) and track_val.upper() != "N/A":
                    track_display = os.path.splitext(os.path.basename(track_val))[0]
                else:
                    track_display = str(current_num)
            alt_bases_lines.append(
                f"Series {key} Base {base} Type {typ} | End {end} Interval {interval//60 if interval else 0}m: Track={track_display} Remaining={remaining}s"
            )

    # --- MESSAGE TIMER REMAINING CALCULATION ---
    if message_timer_last_played and message_timer_value:
        message_timer_remaining = max(0, message_timer_value * 60 - (now - message_timer_last_played))
    else:
        message_timer_remaining = 0

    # --- COS ACTIVITY/REPEATER ACTIVITY STATE ---
    cos_today_minutes = int(round(cos_today_seconds / 60)) if 'cos_today_seconds' in globals() else 0

    # --- Ensure cos_today_date is set before writing ---
    from datetime import datetime
    global cos_today_date
    if not ('cos_today_date' in globals()) or not cos_today_date:
        cos_today_date = datetime.now().strftime("%Y-%m-%d")

    state = {
        "currently_playing": currently_playing,
        "currently_playing_info": currently_playing_info,
        "currently_playing_info_timestamp": currently_playing_info_timestamp,
        "playing_end_time": playing_end_time,
        "playback_status": playback_status,
        "serial_port_missing": serial_port_missing,
        "sound_card_missing": sound_card_missing,
        "serial_history": serial_history[-10:],
        "cos_active": is_cos_active(),
        "remote_device_active": is_remote_busy_active(),
        "uptime": get_drx_uptime(),
        "version": VERSION,

        "random_last_played": {b: random_last_played.get(b, 0) for b in random_bases},
        "random_current_track": {b: os.path.basename(random_current_track.get(b, "")) if random_current_track.get(b, "") else "N/A" for b in random_bases},

        "rotation_last_played": {b: rotation_last_played.get(b, 0) for b in rotation_bases},
        "rotation_current_track": {b: rotation_current_track.get(b, b+1) for b in rotation_bases},

        "sudo_random_last_interval": {b: sudo_random_last_interval.get(b, 0) for b in sudo_bases},
        "sudo_random_interval_track": {b: os.path.basename(sudo_random_interval_track.get(b, "")) if sudo_random_interval_track.get(b, "") else "N/A" for b in sudo_bases},
        "sudo_random_played_in_cycle": {b: [os.path.basename(x) for x in sudo_random_played_in_cycle.get(b, set())] for b in sudo_bases},

        "random_bases_lines": random_bases_lines,
        "rotation_bases_lines": rotation_bases_lines,
        "sudo_bases_lines": sudo_bases_lines,
        "alt_bases_lines": alt_bases_lines,

        "drx_start_time": DRX_START_TIME,
        "updated_at": now,
        "last_played": last_played,

        # --- MESSAGE TIMER FIELDS ---
        "message_timer_last_played": message_timer_last_played,
        "message_timer_value": message_timer_value,
        "message_timer_remaining": int(message_timer_remaining),

        # --- COS/Repeater Activity fields ---
        "cos_today_seconds": cos_today_seconds,
        "cos_today_minutes": cos_today_minutes,
        "cos_today_date": cos_today_date,
    }
    
    # Store state in memory for API access
    with state_lock:
        current_state_memory = state
    
    # NOTE: State writes to disk are disabled - all state is now kept in memory only
    # Activity data is still persisted via the activity file
    # No writes to drx_state.json anymore per requirements

def maybe_run_webcmd():
    global serial_history
    state = {}

    if os.path.exists(WEBCMD_FILE):
        try:
            with open(WEBCMD_FILE, 'r') as f:
                cmd = json.load(f)

            # Add Echo Test command support
            if cmd.get("type") == "echo_test" and "track" in cmd:
                # Queue the echo test command as a string, example: "RE1234"
                track_num = int(cmd["track"])
                debug_log(f"Echo Test requested via web for track {track_num}")
                command_queue.put(f"RE{track_num:04d}")
                log_recent(f"Echo Test: Started for track {track_num} (web)")

                # Update history
                serial_history.insert(0, {
                    "cmd": f"Echo Test: {track_num:04d}",
                    "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "src": "Web"
                })

                # NOTE: State file updates disabled - serial_history is updated in memory via write_state()

            elif cmd.get("type") == "play":
                input_cmd = cmd.get("input", "").strip()
                if input_cmd.lower().endswith('.wav'):
                    source = "web dropdown"
                else:
                    source = "web input box"
                try:
                    command_queue.put(input_cmd)
                    log_recent(f"Play requested: {input_cmd} ({source})")
                    # --- Update global and state serial_history ---
                    serial_history.insert(0, {
                        "cmd": f"> {input_cmd}",
                        "ts": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "src": "Web"
                    })
                    serial_history = serial_history[:10]
                    # NOTE: State file updates disabled - serial_history is updated in memory via write_state()
                except Exception as e:
                    log_recent(f"Play requested: {input_cmd} ({source}) - failed -> {e}")

            elif cmd.get("type") == "stop":
                playback_interrupt.set()
                log_recent("Playback stopped from web")

            elif cmd.get("type") == "reload_config":
                reload_config()
                log_recent("Configuration reload requested from web")

            elif cmd.get("type") == "restart":
                log_recent("DRX script restart requested from web")
                os.remove(WEBCMD_FILE)
                os.execv(sys.executable, [sys.executable] + sys.argv)
                return

            elif cmd.get("type") == "reboot":
                log_recent("System reboot requested from web")
                os.remove(WEBCMD_FILE)
                os.system("reboot")
                return

            os.remove(WEBCMD_FILE)
        except Exception:
            log_exception("maybe_run_webcmd")

def is_terminal():
    # Implementation here...
    return sys.stdin.isatty()

def status_screen(stdscr):
    global serial_buffer, serial_history, currently_playing, currently_playing_info
    global currently_playing_info_timestamp, playing_end_time, playback_status
    global serial_port_missing, sound_card_missing
    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(6, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(7, curses.COLOR_MAGENTA, curses.COLOR_BLACK)  # For remote device active
    curses.curs_set(0)
    stdscr.nodelay(True)
    flash_state = itertools.cycle([True, False])
    while True:
        try:
            stdscr.erase()
            y = 0
            max_y, max_x = stdscr.getmaxyx()
            warning_msgs = []
            if serial_port_missing:
                warning_msgs.append("SERIAL PORT NOT FOUND")
            if sound_card_missing:
                warning_msgs.append("SOUND CARD/DEVICE NOT FOUND")
            if config_warnings and next(flash_state):
                warning_msgs.extend(config_warnings)
            if warning_msgs:
                for line in warning_msgs:
                    if y >= max_y - 2: break
                    stdscr.addstr(y, 0, line[:max_x - 1], curses.color_pair(6) | curses.A_BOLD)
                    y += 1
            if y < max_y - 2:
                stdscr.addstr(y, 0, f"{SCRIPT_NAME} v{VERSION} - Status Screen"[:max_x - 1], curses.color_pair(1))
                y += 1
            if y < max_y - 2:
                y += 1
            if y < max_y - 2:
                stdscr.addstr(y, 0, "Rotation Bases State:"[:max_x - 1], curses.color_pair(2))
                y += 1
            for b, e, t in zip(rotation_bases, rotation_ends, rotation_times):
                if y >= max_y - 2: break
                last = rotation_last_played.get(b, 0)
                current_num = rotation_current_track.get(b, b+1)
                remaining = max(0, t*60 - (time.time() - last))
                msg = f"Base {b} | End {e} Interval {t}: Track={current_num} Remaining={remaining:.1f}s"
                stdscr.addstr(y, 0, msg[:max_x - 1], curses.color_pair(3))
                y += 1
            if y < max_y - 2:
                y += 1
            if y < max_y - 2:
                stdscr.addstr(y, 0, "Random Bases State:"[:max_x - 1], curses.color_pair(2))
                y += 1
            for b, e, t in zip(random_bases, random_ends, random_intervals):
                if y >= max_y - 2: break
                last = random_last_played.get(b, 0)
                current = random_current_track.get(b, 'N/A')
                remaining = max(0, t*60 - (time.time() - last))
                track_name = os.path.basename(current) if current != 'N/A' and current else 'N/A'
                msg = f"Base {b} | End {e} Interval {t}: Track={track_name} Remaining={remaining:.1f}s"
                stdscr.addstr(y, 0, msg[:max_x - 1], curses.color_pair(3))
                y += 1
            if y < max_y - 2:
                y += 1
            if y < max_y - 2:
                stdscr.addstr(y, 0, "SudoRandom Bases State:"[:max_x - 1], curses.color_pair(2))
                y += 1
            for b, e, t in zip(sudo_bases, sudo_ends, sudo_intervals):
                if y >= max_y - 2: break
                last = sudo_random_last_interval.get(b, 0)
                current = sudo_random_interval_track.get(b, 'N/A')
                played = sudo_random_played_in_cycle.get(b, set())
                remaining = max(0, t*60 - (time.time() - last))
                track_name = os.path.basename(current) if current != 'N/A' and current else 'N/A'
                msg = f"Base {b} | End {e} Interval {t}: Track={track_name} Remaining={remaining:.1f}s PlayedInCycle={len(played)}"
                stdscr.addstr(y, 0, msg[:max_x - 1], curses.color_pair(3))
                y += 1
            if y < max_y - 2:
                y += 1
            if y < max_y - 2:
                stdscr.move(y, 0)
                stdscr.clrtoeol()
                # Show last 5 serial_history entries by extracting the "cmd" field:
                serial_display = ' | '.join(
                    ''.join(c for c in (s["cmd"] if isinstance(s, dict) else str(s)) if c in string.printable and c not in '\x1b')
                    for s in serial_history[:5]
                ) if serial_history else 'None'
                stdscr.addstr(y, 0, f"Serial Buffer: {serial_display}"[:max_x - 1], curses.color_pair(5))
                y += 1
            if y < max_y - 2:
                y += 1
            if y < max_y - 2:
                stdscr.move(y, 0)
                stdscr.clrtoeol()
                if playback_status == "Pausing":
                    label = "Currently Pausing:"
                elif playback_status == "Restarting":
                    label = "Currently Pending Restart:"
                else:
                    label = "Currently Playing:"
                info_clean = ''.join(c for c in currently_playing if c in string.printable and c not in '\x1b')
                stdscr.addstr(y, 0, f"{label} {info_clean if info_clean else 'None'}"[:max_x - 1], curses.color_pair(4))
                y += 1
            if currently_playing_info and y < max_y - 2:
                if time.time() - currently_playing_info_timestamp < 5:
                    info2 = ''.join(c for c in currently_playing_info if c in string.printable and c not in '\x1b')
                    stdscr.addstr(y, 0, info2[:max_x - 1], curses.color_pair(4))
                else:
                    # Use status manager to clear expired info
                    status_manager.clear_info_if_expired(5.0)
                y += 1
            if y < max_y - 2:
                y += 1
            # COS and Remote Device Active indicators, side by side
            if y < max_y - 2:
                cos_state = is_cos_active()
                cos_color = curses.color_pair(2) if cos_state else curses.color_pair(5)
                remote_state = is_remote_busy_active()
                remote_color = curses.color_pair(2) if remote_state else curses.color_pair(5)
                stdscr.addstr(y, 0, "COS Active: "[:max_x - 1], curses.color_pair(5))
                stdscr.addstr(f"{'YES' if cos_state else 'NO'}   ", cos_color | curses.A_BOLD)
                stdscr.addstr("Remote Device: "[:max_x - 1], curses.color_pair(5))
                stdscr.addstr(f"{'YES' if remote_state else 'NO'}", remote_color | curses.A_BOLD)
                y += 1
            if y < max_y - 2:
                y += 1
            if y < max_y - 2:
                stdscr.move(y, 0)
                stdscr.clrtoeol()
            # Footer
            stdscr.move(max_y - 1, 0)
            stdscr.clrtoeol()
            stdscr.addstr(max_y - 1, 0, "Press q to quit"[:max_x - 1], curses.color_pair(1))
            stdscr.refresh()
            try:
                if stdscr.getkey() == 'q':
                    break
            except curses.error:
                pass
            time.sleep(0.5)
        except Exception:
            log_exception("status_screen")

def fallback_command_prompt():
    if not sys.stdin.isatty():
        print("No interactive terminal detected. Skipping command prompt loop.")
        return

    print(f"{SCRIPT_NAME} v{VERSION} - Fallback Command Prompt")
    print("Enter DRX commands. Type 'exit' or 'quit' to exit.")
    while True:
        try:
            cmd = input("> ")
            if cmd.strip().lower() in ('exit', 'quit'):
                print("Exiting DRX.")
                sys.exit(0)
            command_queue.put(cmd)
        except KeyboardInterrupt:
            print("\nExiting DRX by Ctrl+C.")
            sys.exit(0)
        except EOFError:
            print("Input closed. Exiting command prompt.")
            break
        except Exception as e:
            print(f"Error: {e}")

def get_drx_uptime():
    now = time.time()
    uptime_seconds = int(now - DRX_START_TIME)
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m {seconds}s"
    elif hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

# Config-dependent values (you must ensure these are set according to your config parsing logic)
REMOTE_BUSY_PIN = get_config_value("GPIO", "remote_busy_pin", fallback=25, cast_func=int)
REMOTE_BUSY_ACTIVE_LEVEL = get_config_value("GPIO", "remote_busy_activate_level", fallback=True, cast_func=bool)
COS_PIN = get_config_value("GPIO", "cos_pin", fallback=23, cast_func=int)
COS_ACTIVE_LEVEL = get_config_value("GPIO", "cos_activate_level", fallback=True, cast_func=bool)
SOUND_DEVICE = get_config_value("Sound", "device", fallback="default")
MAX_COS_INTERRUPTIONS = get_config_value("GPIO", "max_cos_interruptions", fallback=3, cast_func=int)

SOUND_DIRECTORY = get_config_value("Sound", "directory", DEFAULTS["Sound"]["directory"])
SOUND_FILE_EXTENSION = get_config_value("Sound", "extension", DEFAULTS["Sound"]["extension"])
COS_DEBOUNCE_TIME = get_config_value("GPIO", "cos_debounce_time", DEFAULTS["GPIO"]["cos_debounce_time"], float)
MAX_COS_INTERRUPTIONS = get_config_value("GPIO", "max_cos_interruptions", DEFAULTS["GPIO"]["max_cos_interruptions"], int)

SERIAL_PORT = get_config_value("Serial", "port", DEFAULTS["Serial"]["port"])
SERIAL_BAUDRATE = get_config_value("Serial", "baudrate", DEFAULTS["Serial"]["baudrate"], int)
SERIAL_TIMEOUT = get_config_value("Serial", "timeout", DEFAULTS["Serial"]["timeout"], float)
LINE_TIMEOUT = get_config_value("Serial", "line_timeout", DEFAULTS["Serial"]["line_timeout"], float)

RANDOM_BASE = get_config_value("Random", "base", DEFAULTS["Random"]["base"])
RANDOM_END = get_config_value("Random", "end", DEFAULTS["Random"]["end"])
RANDOM_INTERVAL = get_config_value("Random", "interval", DEFAULTS["Random"]["interval"])
ROTATION_BASE = get_config_value("Rotation", "base", DEFAULTS["Rotation"]["base"])
ROTATION_END = get_config_value("Rotation", "end", DEFAULTS["Rotation"]["end"])
ROTATION_TIME = get_config_value("Rotation", "interval", DEFAULTS["Rotation"]["interval"])
SUDORANDOM_BASE = get_config_value("SudoRandom", "base", DEFAULTS["SudoRandom"]["base"])
SUDORANDOM_END = get_config_value("SudoRandom", "end", DEFAULTS["SudoRandom"]["end"])
SUDORANDOM_INTERVAL = get_config_value("SudoRandom", "interval", DEFAULTS["SudoRandom"]["interval"])
#DIRECT_ENABLED = get_config_value("Direct", "enabled", DEFAULTS["Direct"]["enabled"], lambda x: str(x).lower() in ("1", "true", "yes"))
#DIRECT_PREFIX = get_config_value("Direct", "prefix", DEFAULTS["Direct"]["prefix"])
message_timer_value = config.getint('General', 'Message Timer', fallback=1)

random_bases = parse_int_list(RANDOM_BASE, fallback=3000, label="Random base", section="Random")
random_ends = parse_int_list(RANDOM_END, fallback=3099, label="Random end", section="Random")
random_intervals = parse_float_list(RANDOM_INTERVAL, fallback=10, label="Random interval", section="Random")
rotation_bases = parse_int_list(ROTATION_BASE, fallback=4000, label="Rotation base", section="Rotation")
rotation_ends = parse_int_list(ROTATION_END, fallback=4099, label="Rotation end", section="Rotation")
rotation_times = parse_float_list(ROTATION_TIME, fallback=10, label="Rotation time", section="Rotation")
sudo_bases = parse_int_list(SUDORANDOM_BASE, fallback=5000, label="SudoRandom base", section="SudoRandom")
sudo_ends = parse_int_list(SUDORANDOM_END, fallback=5099, label="SudoRandom end", section="SudoRandom")
sudo_intervals = parse_float_list(SUDORANDOM_INTERVAL, fallback=10, label="SudoRandom interval", section="SudoRandom")
random_last_played = {}
random_current_track = {}
rotation_last_played = {}
rotation_current_track = {}

sudo_random_last_interval = {}
sudo_random_interval_track = {}
sudo_random_played_in_cycle = {}
sudo_random_last_file = {}

playback_interrupt = threading.Event()
alternate_sequences = {}

# If you have more code (such as DTMF or web handlers), continue adding here.

def launch_status_screen():
    import curses
    curses.wrapper(status_screen)

def get_previous_day():
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

def parse_minutes_from_activity_log(date_str):
    if not os.path.exists(ACTIVITY_FILE):
        return 0
    with open(ACTIVITY_FILE, "r") as f:
        for line in f:
            if line.startswith(date_str + ","):
                # e.g. line = "2025-06-25,505 minutes"
                try:
                    minutes_part = line.strip().split(",")[1]
                    minutes_num = int(minutes_part.split()[0])
                    return minutes_num
                except Exception:
                    continue
    return 0

def get_wav_sequence_for_number(n):
    """Breaks a number into available wav files: 0-20, then by tens to 100, then by hundreds, etc."""
    if n == 0:
        return ["0.wav"]
    files = []
    remainder = n
    # Big chunks (e.g. 1000, 900, ..., 100)
    for big in [1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]:
        if remainder >= big:
            count = remainder // big
            files += [f"{big}.wav"] * count
            remainder -= big * count
    # Tens (90, 80, ..., 20)
    for ten in [90, 80, 70, 60, 50, 40, 30, 20]:
        if remainder >= ten:
            files.append(f"{ten}.wav")
            remainder -= ten
    # 0-20
    if remainder > 0:
        files.append(f"{remainder}.wav")
    return files

def speak_activity_minutes_for_previous_day():
    global currently_playing, currently_playing_info, currently_playing_info_timestamp, playback_status

    try:
        debug_log("A1 COMMAND: Setting REMOTE_BUSY to active immediately")
        set_remote_busy(True)

        # Only show 'waiting for channel to clear' if COS is active
        if is_cos_active():
            status_manager.set_activity_report("Waiting for channel to clear")
            # Wait for channel to clear (debounce, as before)
            while True:
                if is_cos_active():
                    debug_log("A1 COMMAND: Waiting for COS to become inactive")
                    while is_cos_active():
                        time.sleep(0.1)
                    debug_log("A1 COMMAND: COS has become inactive, starting debounce timer")

                debounce_start = time.time()
                while time.time() - debounce_start < COS_DEBOUNCE_TIME:
                    if is_cos_active():
                        debug_log("A1 COMMAND: COS became active again during debounce period, restarting wait process")
                        break
                    time.sleep(0.1)
                if time.time() - debounce_start >= COS_DEBOUNCE_TIME:
                    debug_log(f"A1 COMMAND: Successfully waited through full debounce period of {COS_DEBOUNCE_TIME} seconds")
                    break
        else:
            # No need to wait, set status to "Playing Activity Report"
            status_manager.set_activity_report("Playing Activity Report")

        # Decide which minutes to announce
        now = datetime.now()
        five_after_midnight = now.replace(hour=0, minute=5, second=0, microsecond=0)
        if now < five_after_midnight:
            # Announce previous day's minutes
            target_date = get_previous_day()
            minutes = parse_minutes_from_activity_log(target_date)
            log_recent(f"Activity Report: Previous day ({target_date}) had {minutes} minutes of activity")
        else:
            # Announce today's minutes so far
            target_date = now.strftime("%Y-%m-%d")
            minutes = cos_today_minutes
            log_recent(f"Activity Report: Today so far ({target_date}) has {minutes} minutes of activity")

        # Prepare the wav sequence
        wavs = []
        wavs.append("activity.wav")
        wavs += get_wav_sequence_for_number(minutes)
        if minutes == 1:
            wavs.append("minute.wav")
        else:
            wavs.append("minutes.wav")

        # Set status to "Playing Activity Report" if not already set
        status_manager.set_activity_report("Playing Activity Report")

        # Play each wav file in order, keep status until end
        for wav in wavs:
            wav_path = os.path.join(EXTRA_SOUND_DIR, wav)
            if os.path.exists(wav_path):
                debug_log(f"A1 COMMAND: Playing {wav_path}")
                play_single_wav(wav_path, interrupt_on_cos=False, block_interrupt=True, reset_status_on_end=False)
            else:
                debug_log(f"A1 COMMAND: WAV file not found: {wav_path}")

        debug_log("A1 COMMAND: Activity report completed")

    except Exception as e:
        debug_log(f"A1 COMMAND: Exception in speak_activity_minutes: {e}")
        log_exception("speak_activity_minutes")
    finally:
        status_manager.set_idle()
        debug_log("A1 COMMAND: Setting REMOTE_BUSY to inactive")
        set_remote_busy(False)

def update_cos_minutes():
    global cos_today_seconds, cos_today_minutes, last_written_minutes, cos_today_date
    cos_today_seconds += 1
    cos_today_minutes = cos_today_seconds // 60
    today_str = datetime.now().strftime("%Y-%m-%d")
    if cos_today_date != today_str:
        cos_today_date = today_str
        cos_today_seconds = 0
        cos_today_minutes = 0
        last_written_minutes = -1  # reset so the log entry is written for new day
    debug_log(f"minutes_rounded: {cos_today_minutes}, last_written_minutes: {last_written_minutes}")
    if cos_today_minutes != last_written_minutes:
        debug_log("Minutes changed, will call prepend_or_replace_today_entry")
        prepend_or_replace_today_entry(cos_today_date, cos_today_minutes)
        last_written_minutes = cos_today_minutes
    save_state()
    write_state()

def prepend_or_replace_today_entry(date_str, minutes_rounded):
    debug_log(f"prepend_or_replace_today_entry called with {date_str}, {minutes_rounded}")
    global ACTIVITY_FILE
    debug_log("Writing activity to:", ACTIVITY_FILE)
    debug_log(f"Called prepend_or_replace_today_entry({date_str}, {minutes_rounded})")
    debug_log(f"ACTIVITY_FILE={ACTIVITY_FILE}")
    os.makedirs(os.path.dirname(ACTIVITY_FILE), exist_ok=True)
    minute_str = "minute" if minutes_rounded == 1 else "minutes"
    entry = f"{date_str},{minutes_rounded} {minute_str}\n"
    lines = []
    # Read current log if it exists
    if os.path.exists(ACTIVITY_FILE):
        with open(ACTIVITY_FILE, "r") as f:
            lines = f.readlines()
        os.chmod(ACTIVITY_FILE, 0o777)
        # Remove any existing entry for this date
        lines = [line for line in lines if not line.startswith(date_str + ",")]
    # Prepend the (possibly updated) entry for today
    lines.insert(0, entry)
    with open(ACTIVITY_FILE, "w") as f:
        f.writelines(lines)
    debug_log(f"prepend_or_replace_today_entry CALLED with {date_str} and {minutes_rounded}")
    debug_log(f"ACTIVITY_FILE is {ACTIVITY_FILE}")   

def save_state():
    """
    DISABLED: State writes to drx_state.json are no longer used.
    Activity data is persisted via the activity file only.
    This function is kept for compatibility but does nothing.
    """
    # No longer write state to file - all state is in memory only
    pass

def parse_temperature_from_wx_data():
    """Reads wx/wx_data and extracts the temperature after 'temperature:'."""
    if not os.path.exists(WX_DATA_FILE):
        return None
    with open(WX_DATA_FILE, "r") as f:
        for line in f:
            if line.startswith("temperature:"):
                try:
                    # e.g. line = "temperature: 74 F"
                    parts = line.strip().split(":")
                    value = parts[1].strip().split()[0]
                    return int(value)
                except Exception:
                    continue
    return None

# --- Flask API Routes ---
@app.route('/api/state')
def get_state():
    """Return current state from memory as JSON."""
    global current_state_memory
    with state_lock:
        # Return copy to avoid modification issues
        return jsonify(current_state_memory.copy())

def run_flask_server():
    """Run Flask server in a separate thread."""
    try:
        # Disable Flask's debug output
        import logging
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        # Suppress Flask startup banner
        cli = sys.modules.get('flask.cli')
        if cli is not None:
            cli.show_server_banner = lambda *x: None
        app.run(host='127.0.0.1', port=API_PORT, debug=False, use_reloader=False)
    except Exception as e:
        debug_log(f"Flask server error: {e}")

def speak_temperature():
    global currently_playing, currently_playing_info, currently_playing_info_timestamp, playback_status

    try:
        debug_log("W2 TEMPERATURE: Setting REMOTE_BUSY to active immediately")
        set_remote_busy(True)

        status_manager.set_weather_report("Temperature Report")

        # Wait for channel to clear (debounce)
        while True:
            if is_cos_active():
                debug_log("W2 TEMPERATURE: Waiting for COS to become inactive")
                while is_cos_active():
                    time.sleep(0.1)
                debug_log("W2 TEMPERATURE: COS has become inactive, starting debounce timer")

            debounce_start = time.time()
            while time.time() - debounce_start < COS_DEBOUNCE_TIME:
                if is_cos_active():
                    debug_log("W2 TEMPERATURE: COS became active again during debounce period, restarting wait process")
                    break
                time.sleep(0.1)
            if time.time() - debounce_start >= COS_DEBOUNCE_TIME:
                debug_log(f"W2 TEMPERATURE: Waited through debounce period of {COS_DEBOUNCE_TIME} seconds")
                break

        # Read temperature
        temp = parse_temperature_from_wx_data()
        if temp is None:
            debug_log("W2 TEMPERATURE: No temperature found in wx/wx_data")
            return

        log_recent(f"Temperature Report: Current temperature is {temp} degrees")

        # Prepare wav sequence
        wavs = []
        wavs.append("call_tempis.wav")  # Play this before the temperature
        wavs += get_wav_sequence_for_number(temp)
        wavs.append("degrees.wav")

        # Play wav files, but do NOT update status during playback, only at start and end.
        for wav in wavs:
            wav_path = os.path.join(EXTRA_SOUND_DIR, wav)
            if os.path.exists(wav_path):
                debug_log(f"W2 TEMPERATURE: Playing {wav_path}")
                play_single_wav(wav_path, interrupt_on_cos=False, block_interrupt=True, reset_status_on_end=False)
            else:
                debug_log(f"W2 TEMPERATURE: WAV file not found: {wav_path}")

        debug_log("W2 TEMPERATURE: Temperature report completed")

    except Exception as e:
        debug_log(f"W2 TEMPERATURE: Exception in speak_temperature: {e}")
        log_exception("speak_temperature")
    finally:
        status_manager.set_idle()
        debug_log("W2 TEMPERATURE: Setting REMOTE_BUSY to inactive")
        set_remote_busy(False)

def parse_wx_conditions_from_wx_data():
    """Reads wx/wx_data and extracts a dict of wx conditions in the requested order, matching field names in the file."""
    wx_fields = [
        "observations",     # 1. conditions (observations)
        "temperature",      # 2. temperature
        "humidity",         # 3. humidity
        "winddir",          # 4. wind direction
        "wind_speed",       # 5. wind speed
        "wind_gust",        # 6. wind gust
        "pressure",         # 7. pressure
        "pressure_status",  # 8. pressure status
        "visibility",       # 9. visibility
        "precipRate",       # 10. precipRate
    ]
    wx_data = { key: None for key in wx_fields }
    if not os.path.exists(WX_DATA_FILE):
        return wx_data
    with open(WX_DATA_FILE, "r") as f:
        for line in f:
            for key in wx_fields:
                if line.lower().startswith(f"{key.lower()}:"):
                    try:
                        value = line.strip().split(":", 1)[1].strip()
                        wx_data[key] = value
                    except Exception:
                        continue
    return wx_data
    
def speak_wx_conditions():
    global currently_playing, currently_playing_info, currently_playing_info_timestamp, playback_status

    try:
        debug_log("W1 CONDITIONS: Setting REMOTE_BUSY to active immediately")
        set_remote_busy(True)

        wx_data = parse_wx_conditions_from_wx_data()
        log_recent(f"WX Report: {wx_data}")

        # Build a summary for the status if you want (optional)
        summary = "Playing weather conditions"

        # Set status ONCE at start of sequence
        status_manager.set_weather_report("WX Conditions Report", summary)

        wavs = []

        # 0. Play HereAre.wav at the beginning
        wavs.append("HereAre.wav")

        # 1. Observations (play the actual wx_data value, e.g. AFewClouds.wav)
        if wx_data.get("observations"):
            obs_wav = (
                wx_data["observations"]
                .replace(" ", "")
                .replace("/", "")
                .replace(".", "")
                .replace("-", "")
                .replace(":", "")
                + ".wav"
            )
            wavs.append(obs_wav)

        # 2. PrecipRate (now in position 2, handles decimals, skips if 0.00)
        if wx_data.get("precipRate"):
            precip_val = wx_data["precipRate"].split()[0]
            try:
                if float(precip_val) != 0.0:
                    wavs.append("precip_rate.wav")
                    if "." in precip_val:
                        whole, frac = precip_val.split(".", 1)
                        wavs += [f"{d}.wav" for d in whole]
                        wavs.append("point.wav")
                        wavs += [f"{d}.wav" for d in frac[:2]]
                    else:
                        wavs += [f"{d}.wav" for d in precip_val]
                    wavs.append("inches_per_hour.wav")
            except Exception:
                pass

        # 3. Temperature (play tempis.wav before value, minus.wav if negative, always play degrees.wav)
        if wx_data.get("temperature"):
            wavs.append("tempis.wav")
            try:
                temp_str = wx_data["temperature"].split()[0]
                if temp_str.startswith('-'):
                    wavs.append("minus.wav")
                    temp_num = int(float(temp_str))
                    wavs += get_wav_sequence_for_number(abs(temp_num))
                else:
                    temp_num = int(float(temp_str))
                    wavs += get_wav_sequence_for_number(temp_num)
                wavs.append("degrees.wav")  # Always add this after the number sequence
            except Exception:
                pass

        # 4. Humidity
        if wx_data.get("humidity"):
            wavs.append("humidity_is.wav")
            try:
                hum_num = int(float(wx_data["humidity"].split()[0]))
                wavs += get_wav_sequence_for_number(hum_num)
                wavs.append("percent.wav")
            except Exception:
                pass

        # 5 & 6. Wind direction and speed combined logic
        wind_speed = None
        if wx_data.get("wind_speed"):
            try:
                wind_speed = int(float(wx_data["wind_speed"].split()[0]))
            except Exception:
                wind_speed = None

        if wind_speed == 0:
            # Say "wind_is.wav calm.wav" and skip wind direction and speed
            wavs.append("wind_is.wav")
            wavs.append("Calm.wav")
        else:
            # Only say wind direction and wind speed if wind is not calm/zero
            if wx_data.get("winddir"):
                wavs.append("wind_is.wav")
                wind_dir_wav = (
                    wx_data["winddir"]
                    .replace(" ", "")
                    .replace("/", "")
                    .replace(".", "")
                    .replace("-", "")
                    .replace(":", "")
                    + ".wav"
                )
                wavs.append(wind_dir_wav)
            if wind_speed is not None:
                wavs.append("at.wav")
                wavs += get_wav_sequence_for_number(wind_speed)
                wavs.append("mph.wav")

        # 7. Wind gust (play guststo.wav before wind_gust, mph.wav after, skip if 0)
        if wx_data.get("wind_gust"):
            try:
                gust_num = int(float(wx_data["wind_gust"].split()[0]))
                if gust_num != 0:
                    wavs.append("guststo.wav")
                    wavs += get_wav_sequence_for_number(gust_num)
                    wavs.append("mph.wav")
            except Exception:
                pass

        # 8. Pressure (play pressure_is.wav before pressure, say decimals, play inches.wav after)
        if wx_data.get("pressure"):
            wavs.append("pressure_is.wav")
            try:
                pres_val = wx_data["pressure"].split()[0]
                if "." in pres_val:
                    whole, frac = pres_val.split(".", 1)
                    wavs += [f"{d}.wav" for d in whole]
                    wavs.append("point.wav")
                    wavs += [f"{d}.wav" for d in frac[:2]]
                else:
                    wavs += [f"{d}.wav" for d in pres_val]
                wavs.append("inches.wav")
            except Exception:
                pass

        # 9. Pressure status
        if wx_data.get("pressure_status"):
            wavs.append("call_pressurestatus.wav")
            pres_status_wav = (
                wx_data["pressure_status"]
                .replace(" ", "")
                .replace("/", "")
                .replace(".", "")
                .replace("-", "")
                .replace(":", "")
                + ".wav"
            )
            wavs.append(pres_status_wav)

        # 10. Visibility (play visibility_is.wav before value)
        if wx_data.get("visibility"):
            wavs.append("visibility_is.wav")
            try:
                vis_num = float(wx_data["visibility"].split()[0])
                vis_int = int(round(vis_num))
                wavs += get_wav_sequence_for_number(vis_int)
                wavs.append("miles.wav")
            except Exception:
                pass

        # Play wav files, but do NOT update status during playback, only at start and end.
        for wav in wavs:
            wav_path = os.path.join(EXTRA_SOUND_DIR, wav)
            if os.path.exists(wav_path):
                debug_log(f"W1 CONDITIONS: Playing {wav_path}")
                play_single_wav(wav_path, interrupt_on_cos=False, block_interrupt=True, reset_status_on_end=False)
            else:
                debug_log(f"W1 CONDITIONS: WAV file not found: {wav_path}")

        debug_log("W1 CONDITIONS: WX report completed")

    except Exception as e:
        debug_log(f"W1 CONDITIONS: Exception in speak_wx_conditions: {e}")
        log_exception("speak_wx_conditions")
    finally:
        # Set status to idle at the end of the sequence
        status_manager.set_idle()
        debug_log("W1 CONDITIONS: Setting REMOTE_BUSY to inactive")
        set_remote_busy(False)

# START OF WX ALERT SECTION

def wx_alert_check(config, debug_log=None):
    """
    Check if weather alerts are enabled and if the alerts file exists.
    Compare the latest issued time to see if it's a new alert.
    
    Args:
        config: ConfigParser object containing the configuration
        debug_log: Debug logging function (optional)
        
    Returns:
        bool: True if alerts are enabled and a NEW alert exists, False otherwise
    """
    global last_alert_time
    
    try:
        # Check if weather alerts are enabled in config
        if config.has_section('WX') and config.getboolean('WX', 'alerts', fallback=False):
            # Check if the wx_alerts file exists
            wx_alerts_path = os.path.join(os.path.dirname(__file__), 'wx', 'wx_alerts')
            if os.path.exists(wx_alerts_path):
                # Read the file and check for latest issued time
                with open(wx_alerts_path, 'r') as f:
                    content = f.read()
                
                # Find all instances of "Issued:" pattern and get the last one
                # Pattern matches "  Issued:      2025-07-13 07:04:00" with flexible spacing
                issued_pattern = r'Issued:\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})'
                matches = re.findall(issued_pattern, content)
                
                if matches:
                    # Get the last (most recent) issued time
                    latest_issued_time = matches[-1]
                    
                    debug_log(f"Latest alert issued time: {latest_issued_time}")
                    
                    # Check if this is a new alert
                    if latest_issued_time != last_alert_time:
                        last_alert_time = latest_issued_time
                        debug_log(f"New weather alert detected: {latest_issued_time}")
                        return True
                    else:
                        debug_log(f"Alert already processed: {latest_issued_time}")
                        return False
                else:
                    debug_log("No 'Issued:' timestamp found in wx_alerts file")
                    
    except Exception as e:
        debug_log(f"Error checking weather alerts: {e}")
    
    return False

def wx_alert_action(config, debug_log=None):
    """
    Perform actions when weather alert is detected.
    This function is called when wx_alerts file contains a new alert.
    
    Args:
        config: ConfigParser object containing the configuration
        debug_log: Debug logging function (optional)
    """
    try:
        if debug_log:
            debug_log("Weather alert action triggered")
        
        # --- CTONE OVERRIDE PATCH ---
        global ctone_override_expire
        wx_alerts = config.getboolean('WX', 'alerts', fallback=False)
        ctone = config.get('WX', 'ctone', fallback='').strip()
        ctone_time = config.getint('WX', 'ctone_time', fallback=0)
        
        if wx_alerts and ctone and ctone.isdigit() and len(ctone) == 4 and ctone_time > 0:
            ctone_override_expire = time.time() + ctone_time * 60
            if debug_log:
                debug_log(f"[CTONE PATCH] Set ctone_override_expire to {ctone_override_expire} for {ctone_time} minutes (now={time.time()})")
        else:
            ctone_override_expire = 0
            if debug_log:
                debug_log(f"[CTONE PATCH] ctone_override_expire cleared or not set. WX: {wx_alerts} ctone: '{ctone}' time: {ctone_time}")

        # Call speak_wx_alerts to announce the alert
        speak_wx_alerts()
        
        # Additional actions can be added here
        # Examples:
        # - Log to file
        # - Send notifications
        # - Update display
        
    except Exception as e:
        if debug_log:
            debug_log(f"Error in wx_alert_action: {e}")

def wx_alert_monitor(config, debug_log=None):
    """
    Monitor for weather alerts at the specified interval (now in seconds).
    This function runs in a separate thread.
    
    Args:
        config: ConfigParser object containing the configuration
        debug_log: Debug logging function (optional)
    """
    try:
        # Get the check interval from config (default to 300 seconds)
        interval_seconds = config.getint('WX', 'interval', fallback=300)
        
        debug_log(f"Weather alert monitoring started. Checking every {interval_seconds} seconds.")
        
        while True:
            if wx_alert_check(config, debug_log):
                wx_alert_action(config, debug_log)
            
            # Wait for the specified interval
            time.sleep(interval_seconds)
            
    except Exception as e:
        debug_log(f"Error in weather alert monitor: {e}")

def start_wx_alert_monitoring(config, debug_log=None):
    """
    Start the weather alert monitoring in a separate thread.
    Call this function from your main program initialization.
    
    Args:
        config: ConfigParser object containing the configuration
        debug_log: Debug logging function (optional)
    """
    try:
        # Only start monitoring if alerts are enabled
        if config.has_section('WX') and config.getboolean('WX', 'alerts', fallback=False):
            # Create and start the monitoring thread
            monitor_thread = threading.Thread(
                target=wx_alert_monitor,
                args=(config, debug_log),
                daemon=True,  # Thread will stop when main program exits
                name="WXAlertMonitor"
            )
            monitor_thread.start()
            debug_log("Weather alert monitoring thread started.")
        else:
            debug_log("Weather alerts are disabled in configuration.")
            # Do not start monitoring if alerts = false
            return
            
    except Exception as e:
        debug_log(f"Error starting weather alert monitoring: {e}")
        
def speak_wx_alerts(*args, **kwargs):
    global currently_playing, currently_playing_info, currently_playing_info_timestamp, playback_status

    try:
        debug_log("W3 ALERTS: Setting REMOTE_BUSY to active immediately")
        set_remote_busy(True)

        # COS debounce logic (from speak_activity_minutes_for_previous_day)
        if is_cos_active():
            status_manager.set_weather_report("Waiting for channel to clear", "")
            # Wait for channel to clear (debounce, as before)
            while True:
                if is_cos_active():
                    debug_log("W3 ALERTS: Waiting for COS to become inactive")
                    while is_cos_active():
                        time.sleep(0.1)
                    debug_log("W3 ALERTS: COS has become inactive, starting debounce timer")

                debounce_start = time.time()
                while time.time() - debounce_start < COS_DEBOUNCE_TIME:
                    if is_cos_active():
                        debug_log("W3 ALERTS: COS became active again during debounce period, restarting wait process")
                        break
                    time.sleep(0.1)
                if time.time() - debounce_start >= COS_DEBOUNCE_TIME:
                    debug_log(f"W3 ALERTS: Successfully waited through full debounce period of {COS_DEBOUNCE_TIME} seconds")
                    break
        else:
            # No need to wait, set status to "WX Alert Report"
            status_manager.set_weather_report("WX Alert Report", "Playing Alert")

        wx_alerts_path = os.path.join(os.path.dirname(__file__), 'wx', 'wx_alerts')
        
        if not os.path.exists(wx_alerts_path):
            debug_log("No wx_alerts file found")
            status_manager.set_weather_report("WX Alert Report", "No active alerts")
            wav_path = os.path.join(EXTRA_SOUND_DIR, "no_wx_alerts.wav")
            if os.path.exists(wav_path):
                play_single_wav(wav_path, interrupt_on_cos=False, block_interrupt=True, reset_status_on_end=False)
            status_manager.set_idle()
            return
        
        # Read the alerts file
        with open(wx_alerts_path, 'r') as f:
            alert_content = f.read()
        
        # Find the last alert block in the file
        alert_blocks = re.split(r'-{10,}', alert_content)
        
        description = None
        issued_time = None
        expires_time = None
        
        # Get the last complete alert block
        for block in reversed(alert_blocks):
            if 'EAS Code:' in block and 'Issued:' in block:
                # Extract key information
                desc_match = re.search(r'Description:\s+(.+)', block)
                issued_match = re.search(r'Issued:\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', block)
                expires_match = re.search(r'Expires:\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', block)
                
                if desc_match:
                    description = desc_match.group(1).strip()
                if issued_match:
                    issued_time = issued_match.group(1)
                if expires_match:
                    expires_time = expires_match.group(1)
                
                if description and issued_time and expires_time:
                    break
        
        if not description:
            debug_log("No valid alert found in wx_alerts file")
            status_manager.set_weather_report("WX Alert Report", "No valid alerts")
            wav_path = os.path.join(EXTRA_SOUND_DIR, "no_wx_alerts.wav")
            if os.path.exists(wav_path):
                play_single_wav(wav_path, interrupt_on_cos=False, block_interrupt=True, reset_status_on_end=False)
            status_manager.set_idle()
            return
        
        debug_log(f"Speaking weather alert - Description: {description}, Issued: {issued_time}, Expires: {expires_time}")
        log_recent(f"WX Alert: {description}")
        
        # Set status ONCE at start of sequence
        status_manager.set_weather_report("WX Alert Report", f"Alert: {description}")
        
        wavs = []
        
        # 1. Start with wx_alert.wav
        wavs.append("wx_alert.wav")
        
        # 2. Build description WAVs with improved lookup logic
        description_wavs = []
        description_lower = description.lower()
        words = description_lower.split()
        
        # First try full description with underscores
        full_desc_wav = f"{description_lower.replace(' ', '_')}.wav"
        full_desc_path = os.path.join(EXTRA_SOUND_DIR, full_desc_wav)
        
        if os.path.exists(full_desc_path):
            description_wavs.append(full_desc_wav)
            debug_log(f"Found full description WAV: {full_desc_wav}")
        else:
            debug_log(f"Full description not found: {full_desc_wav}, trying combinations")
            
            # For multi-word descriptions, try various combinations
            if len(words) > 1:
                # Try different combinations for 3-word descriptions
                if len(words) == 3:
                    # Try first two words joined
                    two_word_wav = f"{words[0]}_{words[1]}.wav"
                    two_word_path = os.path.join(EXTRA_SOUND_DIR, two_word_wav)
                    if os.path.exists(two_word_path):
                        description_wavs.append(two_word_wav)
                        # Then add third word separately
                        third_wav = f"{words[2]}.wav"
                        third_path = os.path.join(EXTRA_SOUND_DIR, third_wav)
                        if os.path.exists(third_path):
                            description_wavs.append(third_wav)
                        else:
                            debug_log(f"Third word not found: {third_wav}")
                    else:
                        # Try last two words joined
                        two_word_wav = f"{words[1]}_{words[2]}.wav"
                        two_word_path = os.path.join(EXTRA_SOUND_DIR, two_word_wav)
                        if os.path.exists(two_word_path):
                            # Add first word separately
                            first_wav = f"{words[0]}.wav"
                            first_path = os.path.join(EXTRA_SOUND_DIR, first_wav)
                            if os.path.exists(first_path):
                                description_wavs.append(first_wav)
                            description_wavs.append(two_word_wav)
                        else:
                            # Fall back to individual words
                            debug_log("No two-word combinations found, using individual words")
                            for word in words:
                                word_wav = f"{word}.wav"
                                word_path = os.path.join(EXTRA_SOUND_DIR, word_wav)
                                if os.path.exists(word_path):
                                    description_wavs.append(word_wav)
                                else:
                                    debug_log(f"Warning: Word not found: {word}")
                
                # For 2-word descriptions, try joined then individual
                elif len(words) == 2:
                    for word in words:
                        word_wav = f"{word}.wav"
                        word_path = os.path.join(EXTRA_SOUND_DIR, word_wav)
                        if os.path.exists(word_path):
                            description_wavs.append(word_wav)
                        else:
                            debug_log(f"Warning: Word not found: {word}")
            else:
                # Single word description
                word_wav = f"{words[0]}.wav"
                word_path = os.path.join(EXTRA_SOUND_DIR, word_wav)
                if os.path.exists(word_path):
                    description_wavs.append(word_wav)
                else:
                    debug_log(f"Warning: Word not found: {words[0]}")
        
        # Add description WAVs to main sequence
        wavs.extend(description_wavs)
        
        # Add repeating.wav and description again
        wavs.append("repeating.wav")
        wavs.extend(description_wavs)
        
        # 3. Speak issued time
        wavs.append("wx_issued.wav")
        
        # Parse issued time: "2025-07-13 07:04:00"
        issued_dt = datetime.strptime(issued_time, "%Y-%m-%d %H:%M:%S")
        
        # Hour
        hour = issued_dt.hour
        if hour == 0:
            wavs.append("0.wav")
        else:
            wavs += get_wav_sequence_for_number(hour)
        
        # Minutes and "hours"
        if issued_dt.minute == 0:
            # For times like 19:00:00, say "100 hours"
            wavs.append("100.wav")
            wavs.append("hours.wav")
        else:
            # Normal minute handling
            if issued_dt.minute < 10:
                wavs.append("oh.wav")
                wavs.append(f"{issued_dt.minute}.wav")
            else:
                wavs += get_wav_sequence_for_number(issued_dt.minute)
            wavs.append("hours.wav")
        
        # "on" for the date
        wavs.append("on.wav")
        
        # Month
        wavs += get_wav_sequence_for_number(issued_dt.month)
        
        # Day
        wavs += get_wav_sequence_for_number(issued_dt.day)
        
        # Year (20 + last two digits)
        wavs.append("20.wav")
        year_last_two = issued_dt.year % 100
        if year_last_two < 10:
            wavs.append("oh.wav")
            wavs.append(f"{year_last_two}.wav")
        else:
            wavs += get_wav_sequence_for_number(year_last_two)
        
        # 4. Speak expires time
        wavs.append("wx_expired.wav")
        
        # Parse expires time
        expires_dt = datetime.strptime(expires_time, "%Y-%m-%d %H:%M:%S")
        
        # at
        wavs.append("at.wav")        
        
        # Hour
        hour = expires_dt.hour
        if hour == 0:
            wavs.append("0.wav")
        else:
            wavs += get_wav_sequence_for_number(hour)
        
        # Minutes and "hours"
        if expires_dt.minute == 0:
            # For times like 19:00:00, say "100 hours"
            wavs.append("hundred.wav")
            wavs.append("hours.wav")
        else:
            # Normal minute handling
            if expires_dt.minute < 10:
                wavs.append("oh.wav")
                wavs.append(f"{expires_dt.minute}.wav")
            else:
                wavs += get_wav_sequence_for_number(expires_dt.minute)
            wavs.append("hours.wav")
        
        # "on" for the date
        wavs.append("on.wav")
        
        # Month
        wavs += get_wav_sequence_for_number(expires_dt.month)
        
        # Day
        wavs += get_wav_sequence_for_number(expires_dt.day)
        
        # Year
        wavs.append("20.wav")
        year_last_two = expires_dt.year % 100
        if year_last_two < 10:
            wavs.append("oh.wav")
            wavs.append(f"{year_last_two}.wav")
        else:
            wavs += get_wav_sequence_for_number(year_last_two)
        
        # Play wav files
        for wav in wavs:
            wav_path = os.path.join(EXTRA_SOUND_DIR, wav)
            if os.path.exists(wav_path):
                debug_log(f"W3 ALERTS: Playing {wav_path}")
                play_single_wav(wav_path, interrupt_on_cos=False, block_interrupt=True, reset_status_on_end=False)
            else:
                debug_log(f"W3 ALERTS: WAV file not found: {wav_path}")

        debug_log("W3 ALERTS: Alert report completed")

    except Exception as e:
        debug_log(f"W3 ALERTS: Exception in speak_wx_alerts: {e}")
        log_exception("speak_wx_alerts")
    finally:
        # Set status to idle at the end of the sequence
        status_manager.set_idle()
        debug_log("W3 ALERTS: Setting REMOTE_BUSY to inactive")
        set_remote_busy(False)
        
def check_wav_exists(wav_file, debug_log=None):
    """
    Check if a WAV file exists in the sounds directory.
    
    Args:
        wav_file: The WAV filename (can include subdirectory like 'extra/flash.wav')
        debug_log: Debug logging function (optional)
    
    Returns:
        bool: True if file exists, False otherwise
    """
    try:
        # Construct the full path
        if wav_file.startswith('extra/'):
            full_path = f"/DRX/sounds/{wav_file}"
        else:
            full_path = f"/DRX/sounds/{wav_file}"
        
        exists = os.path.exists(full_path)
        
        if not exists and debug_log:
            debug_log(f"WAV file not found: {full_path}")
        
        return exists
        
    except Exception as e:
        if debug_log:
            debug_log(f"Error checking WAV file existence: {e}")
        return False

def play_wav_file_with_check(wav_file, debug_log=None):
    """
    Wrapper function to play WAV file with existence check.
    
    Args:
        wav_file: The WAV filename to play
        debug_log: Debug logging function (optional)
    
    Returns:
        bool: True if file was played, False if not found
    """
    if check_wav_exists(wav_file, debug_log):
        play_wav_file(wav_file)
        return True
    else:
        debug_log(f"Cannot play missing WAV file: {wav_file}")
        return False
    """
    Wrapper function to play WAV file with existence check.
    
    Args:
        wav_file: The WAV filename to play
        debug_log: Debug logging function (optional)
    
    Returns:
        bool: True if file was played, False if not found
    """
    if check_wav_exists(wav_file, debug_log):
        play_wav_file(wav_file)
        return True
    else:
        debug_log(f"Cannot play missing WAV file: {wav_file}")
        return False

def activate_ctone_override_from_alert(config):
    """Call this from wx_alert_action or alert logic when alert triggers."""
    global ctone_override_expire
    wx_alerts = config.getboolean('WX', 'alerts', fallback=False)
    ctone = config.get('WX', 'ctone', fallback='').strip()
    ctone_time = config.getint('WX', 'ctone_time', fallback=0)
    if wx_alerts and ctone and ctone.isdigit() and len(ctone) == 4 and ctone_time > 0:
        ctone_override_expire = time.time() + (ctone_time * 60)
        debug_log(f"CTONE OVERRIDE: Activated for {ctone_time} minutes (until {ctone_override_expire})")
    else:
        ctone_override_expire = 0
        debug_log("CTONE OVERRIDE: Not activated (no alert or ctone config)")

def ctone_override_check(code_str):
    """Returns overridden code_str if ctone override is active and pattern matches, else returns code_str unchanged."""
    global ctone_override_expire
    wx_alerts = config.getboolean('WX', 'alerts', fallback=False)
    ctone = config.get('WX', 'ctone', fallback='').strip()
    now = time.time()
    if not (wx_alerts and ctone and ctone.isdigit() and len(ctone) == 4 and now < ctone_override_expire):
        return code_str
    import re
    # Match: four digits, optional suffix, then -CT (e.g., 5050I-CT, 5050-CT)
    m = re.match(r'^(\d{4})([A-Z]*)-CT\b.*', code_str, re.IGNORECASE)
    if m:
        suffix = m.group(2)
        new_code_str = ctone + suffix
        debug_log(f"CTONE OVERRIDE: Substituting {code_str} with {new_code_str} (active)")
        return new_code_str
    return code_str

# END OF WX ALERT SECTION

def handle_tot_start():
    global tot_active, tot_start_time
    with tot_lock:
        if not is_cos_active():
            debug_log("TOT: Ignored, COS not active at start.")
            return
        tot_active = True
        tot_start_time = time.time()
        debug_log("TOT: Timer started.")
        status_manager.set_status(
            status="Time Out Timer",
            playing="TOT Active",
            info="Timing until COS goes inactive"
        )

def handle_tot_stop():
    """Stop TOT timer and record seconds when COS goes inactive."""
    global tot_active, tot_start_time, tot_last_seconds
    with tot_lock:
        if tot_active and tot_start_time:
            tot_last_seconds = int(time.time() - tot_start_time)
            tot_active = False
            tot_start_time = None
            debug_log(f"TOT: Timer stopped, duration: {tot_last_seconds} seconds.")

def monitor_tot_cos():
    """Monitor COS and stop TOT timer when COS goes inactive."""
    prev_cos = is_cos_active()
    while True:
        now_cos = is_cos_active()
        if tot_active and not now_cos and prev_cos:
            handle_tot_stop()
        prev_cos = now_cos
        time.sleep(0.05)
        
def handle_top_command():
    global tot_last_seconds
    status_manager.set_status(
        status="Time Out Seconds",
        playing=f"Timed {tot_last_seconds} seconds",
        info="Reporting time out duration"
    )
    log_recent(f"Status: Time Out Seconds | Currently Playing: Timed {tot_last_seconds} seconds | Info: Reporting time out duration")
    set_remote_busy(True)
    try:
        to1 = os.path.join(EXTRA_SOUND_DIR, "to1.wav")
        if os.path.exists(to1):
            play_single_wav(to1, block_interrupt=True, reset_status_on_end=False)
        wavs = get_wav_sequence_for_number(tot_last_seconds)
        for wav in wavs:
            wav_path = os.path.join(EXTRA_SOUND_DIR, wav)
            if os.path.exists(wav_path):
                play_single_wav(wav_path, block_interrupt=True, reset_status_on_end=False)
        sec_wav = os.path.join(EXTRA_SOUND_DIR, "seconds.wav")
        if os.path.exists(sec_wav):
            play_single_wav(sec_wav, block_interrupt=True, reset_status_on_end=False)
        to2 = os.path.join(EXTRA_SOUND_DIR, "to2.wav")
        if os.path.exists(to2):
            play_single_wav(to2, block_interrupt=True, reset_status_on_end=False)
    finally:
        set_remote_busy(False)
        status_manager.set_idle() 

def reload_config():
    global config, SOUND_DIRECTORY, SOUND_FILE_EXTENSION, SOUND_DEVICE
    global COS_PIN, COS_ACTIVE_LEVEL, REMOTE_BUSY_PIN, REMOTE_BUSY_ACTIVE_LEVEL, COS_DEBOUNCE_TIME, MAX_COS_INTERRUPTIONS
    global SERIAL_PORT, SERIAL_BAUDRATE, SERIAL_TIMEOUT
    global RANDOM_BASE, RANDOM_END, RANDOM_INTERVAL, ROTATION_BASE, ROTATION_END, ROTATION_TIME
    global SUDORANDOM_BASE, SUDORANDOM_END, SUDORANDOM_INTERVAL, DIRECT_ENABLED, DIRECT_PREFIX
    global random_bases, random_ends, random_intervals, rotation_bases, rotation_ends, rotation_times
    global sudo_bases, sudo_ends, sudo_intervals
    global message_timer_value
    global ENABLE_DEBUG_LOGGING

    config.read(config_file_path)
    SOUND_DIRECTORY = get_config_value("Sound", "directory", DEFAULTS["Sound"]["directory"])
    SOUND_FILE_EXTENSION = get_config_value("Sound", "extension", DEFAULTS["Sound"]["extension"])
    SOUND_DEVICE = get_config_value("Sound", "device", DEFAULTS["Sound"]["device"])

    COS_PIN = get_config_value("GPIO", "cos_pin", DEFAULTS["GPIO"]["cos_pin"], int)
    COS_ACTIVE_LEVEL = get_config_value("GPIO", "cos_activate_level", DEFAULTS["GPIO"]["cos_activate_level"], lambda x: str(x).lower() in ('1', 'true', 'yes'))
    REMOTE_BUSY_PIN = get_config_value("GPIO", "remote_busy_pin", DEFAULTS["GPIO"]["remote_busy_pin"], int)
    REMOTE_BUSY_ACTIVE_LEVEL = get_config_value(
        "GPIO",
        "remote_busy_activate_level",
        DEFAULTS["GPIO"]["remote_busy_activate_level"],
        str_to_bool
    )
    debug_log("REMOTE_BUSY_ACTIVE_LEVEL (from config):", REMOTE_BUSY_ACTIVE_LEVEL)
    COS_DEBOUNCE_TIME = get_config_value(
        "GPIO",
        "cos_debounce_time",
        DEFAULTS["GPIO"]["cos_debounce_time"],
        float
    )
    MAX_COS_INTERRUPTIONS = get_config_value("GPIO", "max_cos_interruptions", DEFAULTS["GPIO"]["max_cos_interruptions"], int)

    SERIAL_PORT = get_config_value("Serial", "port", DEFAULTS["Serial"]["port"])
    SERIAL_BAUDRATE = get_config_value("Serial", "baudrate", DEFAULTS["Serial"]["baudrate"], int)
    SERIAL_TIMEOUT = get_config_value("Serial", "timeout", DEFAULTS["Serial"]["timeout"], float)

    RANDOM_BASE = get_config_value("Random", "base", DEFAULTS["Random"]["base"])
    RANDOM_END = get_config_value("Random", "end", DEFAULTS["Random"]["end"])
    RANDOM_INTERVAL = get_config_value("Random", "interval", DEFAULTS["Random"]["interval"])
    ROTATION_BASE = get_config_value("Rotation", "base", DEFAULTS["Rotation"]["base"])
    ROTATION_END = get_config_value("Rotation", "end", DEFAULTS["Rotation"]["end"])
    ROTATION_TIME = get_config_value("Rotation", "interval", DEFAULTS["Rotation"]["interval"])
    SUDORANDOM_BASE = get_config_value("SudoRandom", "base", DEFAULTS["SudoRandom"]["base"])
    SUDORANDOM_END = get_config_value("SudoRandom", "end", DEFAULTS["SudoRandom"]["end"])
    SUDORANDOM_INTERVAL = get_config_value("SudoRandom", "interval", DEFAULTS["SudoRandom"]["interval"])
    #DIRECT_ENABLED = get_config_value("Direct", "enabled", DEFAULTS["Direct"]["enabled"], lambda x: str(x).lower() in ("1", "true", "yes"))
    #DIRECT_PREFIX = get_config_value("Direct", "prefix", DEFAULTS["Direct"]["prefix"])

    random_bases[:] = parse_int_list(RANDOM_BASE, fallback=3000, label="Random base", section="Random")
    random_ends[:] = parse_int_list(RANDOM_END, fallback=3099, label="Random end", section="Random")
    random_intervals[:] = parse_float_list(RANDOM_INTERVAL, fallback=10, label="Random interval", section="Random")
    rotation_bases[:] = parse_int_list(ROTATION_BASE, fallback=4000, label="Rotation base", section="Rotation")
    rotation_ends[:] = parse_int_list(ROTATION_END, fallback=4099, label="Rotation end", section="Rotation")
    rotation_times[:] = parse_float_list(ROTATION_TIME, fallback=10, label="Rotation time", section="Rotation")
    sudo_bases[:] = parse_int_list(SUDORANDOM_BASE, fallback=5000, label="SudoRandom base", section="SudoRandom")
    sudo_ends[:] = parse_int_list(SUDORANDOM_END, fallback=5099, label="SudoRandom end", section="SudoRandom")
    sudo_intervals[:] = parse_float_list(SUDORANDOM_INTERVAL, fallback=10, label="SudoRandom interval", section="SudoRandom")

    message_timer_value = parse_message_timer(get_config_value("General", "Message Timer", "N"))
    ENABLE_DEBUG_LOGGING = get_config_value("Debug", "enable_debug_logging", fallback=False, cast_func=str_to_bool)

def is_terminal():
    return sys.stdin.isatty() and "TERM" in os.environ and os.environ["TERM"] != "unknown"

def main():
    try:
        gpio_setup()
        validate_config_pairs()
        global serial_port, serial_port_missing
        try:
            serial_port = serial.Serial(
                port=get_config_value("Serial", "port", fallback="/dev/ttyUSB0"),
                baudrate=get_config_value("Serial", "baudrate", fallback=9600, cast_func=int),
                timeout=get_config_value("Serial", "timeout", fallback=0.5, cast_func=float)
            )
            serial_port.reset_input_buffer()
            serial_port_missing = False
        except Exception as e:
            serial_port = None
            serial_port_missing = True

        load_state()
        
        # Initialize status manager with callback
        global status_manager
        status_manager = PlaybackStatusManager(write_state)
        status_manager.register_status_callback(sync_legacy_status_variables)
        
        # Start weather alert monitoring if enabled
        if config.has_section('WX') and config.getboolean('WX', 'alerts', fallback=False):
            start_wx_alert_monitoring(config, debug_log)
        
        try:
            threading.Thread(target=serial_read_loop, daemon=True).start()
            threading.Thread(target=process_serial_commands, daemon=True).start()
            threading.Thread(target=bg_write_state_and_webcmd_loop, daemon=True).start()
            threading.Thread(target=bg_cos_state_update_loop, daemon=True).start()
            threading.Thread(target=command_processor_loop, daemon=True).start()
            threading.Thread(target=dtmf_cos_edge_monitor, daemon=True).start()
            threading.Thread(target=monitor_cos, daemon=True).start()
            threading.Thread(target=run_flask_server, daemon=True).start()
            threading.Thread(target=monitor_tot_cos, daemon=True).start()

            if is_terminal():
                try:
                    curses.wrapper(status_screen)
                except Exception:
                    print("Curses UI failed to start. Falling back to command prompt mode.")
                    log_exception("main (curses fallback)")
                    fallback_command_prompt()
                    while True:
                        time.sleep(1)
            else:
                print("No terminal detected. Running in headless mode.")
                fallback_command_prompt()
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass
        except Exception:
            log_exception("main")
    finally:
        try:
            gpio_cleanup()
        except Exception:
            log_exception("main (lgpio cleanup)")

if __name__ == "__main__":
    main()       
