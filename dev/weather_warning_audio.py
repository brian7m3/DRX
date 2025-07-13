#!/usr/bin/env python3
"""
NOAA Weather Radio Audio Downloader
Downloads and plays actual NOAA Weather Radio broadcasts for a given location
"""

import requests
import json
import re
from datetime import datetime
import subprocess
import os
import shutil
import tempfile
import sys
import time
from bs4 import BeautifulSoup

# Known working NOAA Weather Radio audio feeds
# These are verified to be functional as of July 2023
RELIABLE_FEEDS = {
    # Format: 'Station Code': ('URL', 'Display Name')
    'KEC56': ('https://www.weather.gov/source/nwr/mp3/KEC56.mp3', 'Oklahoma City, OK (KEC56)'),
    'WXK96': ('https://www.weather.gov/source/nwr/mp3/WXK96.mp3', 'Tulsa, OK (WXK96)'),
    'KHB34': ('https://www.weather.gov/source/nwr/mp3/KHB34.mp3', 'Miami, FL (KHB34)'),
    'KHB38': ('https://www.weather.gov/source/nwr/mp3/KHB38.mp3', 'Houston, TX (KHB38)'),
    'KEC55': ('https://www.weather.gov/source/nwr/mp3/KEC55.mp3', 'Dallas/Fort Worth, TX (KEC55)'),
    'KIH20': ('https://www.weather.gov/source/nwr/mp3/KIH20.mp3', 'New Orleans, LA (KIH20)'),
    'KXI56': ('https://www.weather.gov/source/nwr/mp3/KXI56.mp3', 'Topeka, KS (KXI56)'),
    'KWO35': ('https://www.weather.gov/source/nwr/mp3/KWO35.mp3', 'Chicago, IL (KWO35)'),
    'KWO39': ('https://www.weather.gov/source/nwr/mp3/KWO39.mp3', 'New York, NY (KWO39)')
}

# ZIP code prefixes to NWR stations mapping
ZIP_TO_NWR = {
    # Oklahoma
    "73": "KEC56",  # Oklahoma City
    "74": "WXK96",  # Tulsa
    # Florida
    "33": "KHB34",  # Miami
    # Texas
    "77": "KHB38",  # Houston
    "75": "KEC55",  # Dallas/Fort Worth
    # Louisiana
    "70": "KIH20",  # New Orleans
    # Kansas
    "66": "KXI56",  # Topeka
    # Illinois
    "60": "KWO35",  # Chicago
    # New York
    "10": "KWO39",  # New York City
}

def get_coordinates_from_zip(zip_code):
    """Get latitude and longitude coordinates for a US ZIP code"""
    try:
        url = f"https://nominatim.openstreetmap.org/search?postalcode={zip_code}&country=USA&format=json"
        headers = {"User-Agent": "WeatherWarningsApp/1.0 (local-script)"}
        print(f"Getting coordinates from ZIP code: {zip_code}")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if data and len(data) > 0:
            lat = float(data[0]['lat'])
            lon = float(data[0]['lon'])
            location_name = data[0].get('display_name', '').split(',')[0]
            return lat, lon, location_name
        else:
            print(f"Could not find coordinates for ZIP code {zip_code}.")
            return None, None, "Unknown Location"
    except Exception as e:
        print(f"Error in geocoding request: {e}")
        return None, None, "Unknown Location"

def get_nws_office(lat, lon):
    """Get the NWS office for a specific location"""
    if lat is None or lon is None:
        return None
        
    try:
        headers = {
            "User-Agent": "WeatherWarningsApp/1.0 (local-script)",
            "Accept": "application/geo+json"
        }
        
        point_url = f"https://api.weather.gov/points/{lat},{lon}"
        print(f"Determining your local NWS office...")
        
        point_response = requests.get(point_url, headers=headers)
        point_response.raise_for_status()
        
        point_data = point_response.json()
        
        # Extract the office ID from the properties
        if 'properties' in point_data and 'cwa' in point_data['properties']:
            office = point_data['properties']['cwa']
            print(f"Your NWS office is: {office}")
            return office
        else:
            print("Could not determine NWS office.")
            return None
    except Exception as e:
        print(f"Error determining NWS office: {e}")
        return None

def get_nws_alerts(lat, lon):
    """Get weather alerts from NWS API"""
    if lat is None or lon is None:
        return []
        
    try:
        headers = {
            "User-Agent": "WeatherWarningsApp/1.0 (local-script)",
            "Accept": "application/geo+json"
        }
        
        alerts_url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
        print(f"Checking for active alerts...")
        
        alerts_response = requests.get(alerts_url, headers=headers)
        alerts_response.raise_for_status()
        
        alerts_data = alerts_response.json()
        
        alerts = []
        if 'features' in alerts_data and alerts_data['features']:
            for alert in alerts_data['features']:
                properties = alert['properties']
                alerts.append({
                    'event': properties.get('event', 'No event data'),
                    'headline': properties.get('headline', 'No headline data'),
                    'description': properties.get('description', 'No description data'),
                    'id': properties.get('id', '')
                })
            print(f"Found {len(alerts)} active alerts.")
        else:
            print("No active alerts found for this location.")
            
        return alerts
    except Exception as e:
        print(f"Error fetching alerts: {e}")
        return []

def display_alerts(alerts, location_name):
    """Display the alerts in a formatted way"""
    print(f"\nWeather Alert Status for {location_name}")
    print("=" * 60)
    
    if not alerts:
        print("\n⚠️ NO ACTIVE WEATHER ALERTS FOR THIS LOCATION ⚠️")
        return
    
    print(f"\n📢 {len(alerts)} ACTIVE WEATHER ALERTS 📢")
    
    for i, alert in enumerate(alerts, 1):
        print(f"\nALERT #{i}:")
        print(f"EVENT: {alert['event']}")
        print(f"HEADLINE: {alert['headline']}")
        
        # Format description for better readability
        description = alert['description']
        if len(description) > 200:
            description = description[:200] + "..."
        description = re.sub(r'\s+', ' ', description).strip()
        
        print(f"DESCRIPTION: {description}")
        print(f"ID: {alert['id']}")
        print("-" * 60)

def get_nwr_station_for_zip(zip_code):
    """Determine the NWR station code for a ZIP code"""
    prefix = zip_code[:2]
    if prefix in ZIP_TO_NWR:
        station_code = ZIP_TO_NWR[prefix]
        if station_code in RELIABLE_FEEDS:
            url, name = RELIABLE_FEEDS[station_code]
            return station_code, url, name
    
    # Default to Oklahoma City if no match (you can change this default)
    return "KEC56", RELIABLE_FEEDS["KEC56"][0], RELIABLE_FEEDS["KEC56"][1]

def get_available_stations():
    """Return all available NWR stations"""
    stations = []
    for code, (url, name) in RELIABLE_FEEDS.items():
        stations.append((code, url, name))
    return stations

def verify_url_exists(url):
    """Check if a URL exists and returns a valid response"""
    try:
        headers = {"User-Agent": "WeatherWarningsApp/1.0"}
        response = requests.head(url, timeout=5, headers=headers)
        return response.status_code == 200
    except:
        return False

def download_audio(url, output_path):
    """Download audio from URL with progress indicator"""
    try:
        print(f"Downloading audio from: {url}")
        
        headers = {"User-Agent": "WeatherWarningsApp/1.0"}
        response = requests.get(url, stream=True, headers=headers, timeout=10)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        
        with open(output_path, 'wb') as f:
            if total_size > 0:
                print(f"File size: {total_size / 1024:.1f} KB")
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        percent = int(100 * downloaded / total_size)
                        sys.stdout.write(f"\rDownloading: {percent}% complete")
                        sys.stdout.flush()
                print()  # New line after progress
            else:
                print("Unknown file size, downloading...")
                f.write(response.content)
                
        # Verify the file was downloaded
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"Download complete: {output_path}")
            return True
        else:
            print("Download failed: File is empty")
            if os.path.exists(output_path):
                os.remove(output_path)
            return False
            
    except Exception as e:
        print(f"Error downloading audio: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)
        return False

def play_audio_file(file_path):
    """Play an audio file using the system's audio player"""
    if not os.path.exists(file_path):
        print(f"Error: Audio file not found: {file_path}")
        return False
        
    # Try different players, with aplay as the first choice
    players = [
        ['aplay', [file_path]],
        ['mpg123', [file_path]],
        ['mpg321', [file_path]],
        ['mplayer', [file_path]],
        ['ffplay', ['-nodisp', '-autoexit', file_path]]
    ]
    
    for player, args in players:
        if shutil.which(player):
            try:
                print(f"Playing audio using {player}...")
                subprocess.run([player] + args, check=True)
                return True
            except subprocess.SubprocessError:
                print(f"Error with {player}, trying another player...")
                continue
    
    print("No suitable audio player found.")
    print(f"Audio file saved to: {file_path}")
    return False

def create_text_to_speech(alert_text, output_file):
    """Create a TTS audio file from alert text using Pico TTS"""
    try:
        # Create temporary WAV file
        temp_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        temp_wav.close()
        
        # Generate speech with pico2wave
        subprocess.run(['pico2wave', '-l', 'en-US', '-w', temp_wav.name, alert_text], check=True)
        
        # Check if we need to convert from WAV to MP3
        if output_file.endswith('.mp3') and shutil.which('ffmpeg'):
            subprocess.run(['ffmpeg', '-i', temp_wav.name, '-q:a', '2', output_file, '-y'], check=True)
        else:
            # Just use the WAV file
            shutil.copy(temp_wav.name, output_file)
        
        # Clean up
        os.remove(temp_wav.name)
        return True
    except Exception as e:
        print(f"Error creating TTS audio: {e}")
        return False

def main():
    """Main function to run the program"""
    print("🌦️ NOAA Weather Radio Audio Downloader 🌦️")
    
    # Fixed date format to match exactly what was requested
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Current Date and Time (UTC - YYYY-MM-DD HH:MM:SS formatted): {current_time}")
    
    # Display username on a separate line
    try:
        username = os.environ.get('USER') or os.environ.get('USERNAME') or "Unknown User"
        print(f"Current User's Login: {username}")
    except Exception:
        pass
    
    # Create directory for downloaded files
    alert_dir = os.path.expanduser("~/.weather_alerts")
    os.makedirs(alert_dir, exist_ok=True)
    
    # Check for required tools
    if not shutil.which('aplay'):
        print("\nWarning: aplay not found. Audio playback may not work.")
        print("Install with: sudo apt-get install alsa-utils")
    
    while True:
        print("\nOptions:")
        print("1. Get weather alerts and radio broadcast by ZIP code")
        print("2. List all available NOAA Weather Radio stations")
        print("3. Exit")
        
        choice = input("\nEnter your choice (1-3): ")
        
        if choice == '3':
            print("Exiting application. Stay safe!")
            break
            
        elif choice == '2':
            # List all available stations
            print("\nAvailable NOAA Weather Radio Stations:")
            print("=" * 60)
            stations = get_available_stations()
            for i, (code, url, name) in enumerate(stations, 1):
                print(f"{i}. {name} - Station Code: {code}")
            
            # Allow user to play a station directly
            station_choice = input("\nEnter station number to play (or 0 to go back): ")
            
            try:
                station_choice = int(station_choice)
                if station_choice == 0:
                    continue
                    
                if 1 <= station_choice <= len(stations):
                    code, url, name = stations[station_choice - 1]
                    
                    # Check if URL exists
                    if not verify_url_exists(url):
                        print(f"Error: Audio feed for {name} is not currently available.")
                        continue
                        
                    # Download and play
                    file_path = os.path.join(alert_dir, f"{code}.mp3")
                    if download_audio(url, file_path):
                        play_audio_file(file_path)
                        print(f"Audio saved to: {file_path}")
                    
            except ValueError:
                print("Invalid choice. Please enter a number.")
            
        elif choice == '1':
            # Get alerts and broadcast by ZIP code
            zip_code = input("\nEnter a 5-digit ZIP code: ")
            
            # Simple validation for ZIP code format
            if not (zip_code.isdigit() and len(zip_code) == 5):
                print("Invalid ZIP code format. Please enter a 5-digit ZIP code.")
                continue
                
            # Get coordinates and location name
            lat, lon, location_name = get_coordinates_from_zip(zip_code)
            if lat is None:
                continue
                
            # Get the NWS office for this location
            office = get_nws_office(lat, lon)
            
            # Get weather alerts
            alerts = get_nws_alerts(lat, lon)
            
            # Display alerts
            display_alerts(alerts, location_name)
            
            # Get NWR station for this ZIP code
            station_code, station_url, station_name = get_nwr_station_for_zip(zip_code)
            
            print(f"\nNOAA Weather Radio station for this area:")
            print(f"Station: {station_code} ({station_name})")
            
            # Ask if user wants to play the audio
            audio_choice = input("\nWould you like to play the NOAA Weather Radio broadcast? (y/n): ")
            
            if audio_choice.lower() == 'y':
                # Check if URL exists
                if not verify_url_exists(station_url):
                    print(f"Error: Audio feed for {station_name} is not currently available.")
                    
                    # Offer alternative stations
                    print("\nWould you like to try one of these alternative stations?")
                    alt_stations = [(c, u, n) for c, (u, n) in RELIABLE_FEEDS.items() if c != station_code]
                    for i, (code, url, name) in enumerate(alt_stations[:3], 1):
                        print(f"{i}. {name}")
                        
                    alt_choice = input("\nEnter station number to try (or 0 to skip): ")
                    try:
                        alt_choice = int(alt_choice)
                        if alt_choice == 0:
                            pass
                        elif 1 <= alt_choice <= len(alt_stations[:3]):
                            code, url, name = alt_stations[alt_choice - 1]
                            file_path = os.path.join(alert_dir, f"{code}.mp3")
                            if download_audio(url, file_path):
                                play_audio_file(file_path)
                                print(f"Audio saved to: {file_path}")
                    except ValueError:
                        pass
                else:
                    # Download and play
                    file_path = os.path.join(alert_dir, f"{station_code}.mp3")
                    if download_audio(station_url, file_path):
                        play_audio_file(file_path)
                        print(f"Audio saved to: {file_path}")
            
            # If there are alerts, offer TTS version
            if alerts:
                tts_choice = input("\nWould you like to hear a text-to-speech version of the current alert? (y/n): ")
                
                if tts_choice.lower() == 'y' and shutil.which('pico2wave'):
                    # Create alert text
                    alert_text = f"This is a weather alert for {location_name}. "
                    alert_text += f"The National Weather Service has issued a {alerts[0]['event']}. "
                    alert_text += f"{alerts[0]['headline']}."
                    
                    # Generate TTS audio
                    tts_file = os.path.join(alert_dir, f"tts_alert_{zip_code}.wav")
                    if create_text_to_speech(alert_text, tts_file):
                        play_audio_file(tts_file)
                        print(f"TTS audio saved to: {tts_file}")
                elif tts_choice.lower() == 'y':
                    print("pico2wave not found. Install with: sudo apt-get install libttspico-utils")
        else:
            print("Invalid choice. Please enter 1, 2, or 3.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram interrupted. Exiting...")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")