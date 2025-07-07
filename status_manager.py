#!/usr/bin/env python3
"""
PlaybackStatusManager - Centralized status management for DRX

This module provides a thread-safe, centralized system for managing playback status
in the DRX system. It replaces direct assignments to global status variables with
a single management interface that supports:

- Fine-grained, arbitrary status/info updates
- Section context for base tracks
- Automatic write_state() handling  
- Callback mechanism for legacy global variable sync
- Thread-safety
- Extensibility for future status types
"""

import threading
import time
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
            self._currently_playing = playing if playing is not None else status
            
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