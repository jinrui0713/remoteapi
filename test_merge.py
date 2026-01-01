
import yt_dlp
import os
import json

url = "https://www.youtube.com/watch?v=jNQXAC9IVRw" # Me at the zoo
download_dir = "test_downloads"
os.makedirs(download_dir, exist_ok=True)

# Mocking the logic in main.py
req_type = 'video'
req_quality = 'best'
req_audio_format = 'mp3'

ydl_opts = {
    'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),
    'quiet': False,
    'no_warnings': True,
    'writethumbnail': False,
    'restrictfilenames': True, # Ensure filenames are safe (ASCII, no spaces)
    'windowsfilenames': True, # Force Windows-compatible filenames
}

# Format selection
if req_type == 'audio':
    ydl_opts['format'] = 'bestaudio/best'
    ydl_opts['postprocessors'] = [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': req_audio_format,
        'preferredquality': '192',
    }]
else:
    # Video format
    # Force MP4 merge to ensure browser compatibility and predictable extension
    ydl_opts['merge_output_format'] = 'mp4'
    
    if req_quality == 'best':
        ydl_opts['format'] = 'bestvideo+bestaudio/best'
    else:
        # Try to get specific height, fallback to best
        ydl_opts['format'] = f'bestvideo[height<={req_quality}]+bestaudio/best[height<={req_quality}]/best'

print("Downloading...")
try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        
        print("\n--- Info Keys ---")
        # print(info.keys())
        
        print("\n--- Requested Downloads ---")
        if 'requested_downloads' in info:
            for d in info['requested_downloads']:
                print(f"Filepath: {d.get('filepath')}")
                print(f"Filename: {d.get('filename')}")
        else:
            print("No requested_downloads found.")

        print("\n--- Direct Info ---")
        print(f"Filepath: {info.get('filepath')}")
        print(f"Ext: {info.get('ext')}")
        
        print("\n--- Prepare Filename ---")
        prepared_filename = ydl.prepare_filename(info)
        print(f"Prepared: {prepared_filename}")

        # Logic from main.py
        final_filename = None
        if 'requested_downloads' in info:
            for d in info['requested_downloads']:
                if 'filepath' in d:
                    final_filename = d['filepath']
        
        if not final_filename:
            final_filename = info.get('filepath')
        
        if not final_filename:
            final_filename = prepared_filename

        print(f"\nDetected Final Filename: {final_filename}")
        
        if final_filename and os.path.exists(final_filename):
            print("File EXISTS at detected path.")
        else:
            print("File NOT FOUND at detected path.")

        # Check what actually exists
        print("\n--- Directory Listing ---")
        for f in os.listdir(download_dir):
            print(f)

except Exception as e:
    print(f"Error: {e}")
