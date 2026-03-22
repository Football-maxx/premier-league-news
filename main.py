#!/usr/bin/env python3
"""
Automated Premier League Cartoon News System
Runs on GitHub Actions (free)
"""

import os
import sys
import requests
import json
import base64
import time
from datetime import datetime
from moviepy import VideoClip, AudioFileClip, VideoFileClip, CompositeVideoClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import replicate
from supabase import create_client, Client
import random

# ---------- CONFIGURATION (ENVIRONMENT VARIABLES) ----------
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
VOICERSS_API_KEY = os.environ.get("VOICERSS_API_KEY")
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
YOUTUBE_TOKEN_JSON = os.environ.get("YOUTUBE_TOKEN_JSON")
# ------------------------------------------------------------

# Initialize debug log file
DEBUG_LOG = "debug.log"
with open(DEBUG_LOG, "w") as f:
    f.write("Debug log started\n")

def debug_print(msg):
    """Print to console and write to debug log."""
    print(msg)
    sys.stdout.flush()
    with open(DEBUG_LOG, "a") as f:
        f.write(msg + "\n")
        f.flush()

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

def init_db():
    """Table is created manually in Supabase; no need to create here."""
    pass

def fetch_matches():
    """Get today's Premier League matches from football-data.org"""
    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        debug_print(f"Error fetching matches: {e}")
        return

    # Get already posted matches from Supabase
    posted_response = supabase.table("matches").select("fixture_id").eq("posted", 1).execute()
    posted_ids = [row['fixture_id'] for row in posted_response.data]

    for match in data.get('matches', []):
        if match.get('competition', {}).get('code') != 'PL':
            continue

        fixture_id = match['id']
        home = match['homeTeam']['name']
        away = match['awayTeam']['name']
        date = match['utcDate']
        status = match['status']
        home_score = match['score']['fullTime']['home'] or 0
        away_score = match['score']['fullTime']['away'] or 0

        # Check if already posted
        if fixture_id in posted_ids:
            continue

        # Insert or update match in Supabase
        data_row = {
            "fixture_id": fixture_id,
            "home_team": home,
            "away_team": away,
            "match_date": date,
            "status": status,
            "home_score": home_score,
            "away_score": away_score,
            "posted": 0
        }
        supabase.table("matches").upsert(data_row, on_conflict="fixture_id").execute()

        # If match finished, process it
        if status == 'FINISHED':
            debug_print(f"Processing finished match: {home} vs {away}")
            process_match(fixture_id, home, away, home_score, away_score)
            # Mark as posted
            supabase.table("matches").update({"posted": 1}).eq("fixture_id", fixture_id).execute()

def get_match_goals(fixture_id):
    """Get goals from Football-Data.org for a specific match."""
    debug_print(f"DEBUG: get_match_goals called for fixture {fixture_id}")
    url = f"https://api.football-data.org/v4/matches/{fixture_id}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        debug_print(f"Could not fetch match details: {e}")
        return []

    goals = []
    for goal in data.get('goals', []):
        scorer = goal.get('scorer', {}).get('name')
        if scorer:
            goals.append({
                'player': scorer,
                'minute': goal.get('minute'),
                'team': goal.get('team', {}).get('name')
            })
    debug_print(f"DEBUG: get_match_goals returning {len(goals)} goals")
    return goals

def process_match(fixture_id, home, away, h_score, a_score):
    """Generate video using only cartoon clips (no anchor)."""
    debug_print(f"DEBUG: process_match called with fixture_id={fixture_id}")
    # 1. Generate script and audio
    script = generate_script(home, away, h_score, a_score)
    audio_file = f"audio_{fixture_id}.mp3"
    generate_audio(script, audio_file)

    # 2. Get goals for this match
    goals = get_match_goals(fixture_id)
    debug_print(f"DEBUG: goals after get_match_goals: {goals}")

    # 3. Build video from clips (without anchor)
    final_video = f"final_{fixture_id}.mp4"
    build_video_from_clips(goals, audio_file, final_video)

    # 4. Upload
    title = f"Premier League Result: {home} {h_score} – {a_score} {away} - {datetime.now().strftime('%Y%m%d-%H%M')}"
    upload_to_youtube(final_video, title)

def generate_script(home, away, h_score, a_score):
    return f"Hello football fans! Here's the latest Premier League result. {home} {h_score} – {a_score} {away}. That's all for now. Don't forget to like and subscribe!"

def build_video_from_clips(goals, audio_file, output_path):
    """
    Create a video by concatenating the four cartoon clips in order,
    with the scorer's name/minute overlaid on the goal_to_net clip.
    The video duration is trimmed to match the audio length.
    """
    debug_print("DEBUG: build_video_from_clips started")

    # Load audio to get its duration
    audio_clip = AudioFileClip(audio_file)
    audio_duration = audio_clip.duration
    debug_print(f"DEBUG: audio duration = {audio_duration} seconds")

    # Define the sequence of clips (in the desired order)
    clip_sequence = [
        "assets/clips/football_news.mp4",
        "assets/clips/football_with_players.mp4",
        "assets/clips/goal_to_net.mp4",
        "assets/clips/celebration.mp4"
    ]

    all_clips = []

    for idx, clip_path in enumerate(clip_sequence):
        if not os.path.exists(clip_path):
            debug_print(f"WARNING: Clip not found: {clip_path}")
            continue

        try:
            clip = VideoFileClip(clip_path)
            debug_print(f"Loaded {clip_path}, duration={clip.duration}")

            # For the goal_to_net clip, add text overlay if there are goals
            if clip_path == "assets/clips/goal_to_net.mp4" and goals:
                # Use the first goal (for simplicity; you can iterate if multiple)
                goal = goals[0]
                text_str = f"{goal['player']} – {goal['minute']}'"

                # Create text overlay using PIL (frame‑by‑frame)
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf", 40)
                except:
                    font = ImageFont.load_default()

                def make_text_frame(t):
                    img = Image.new('RGBA', (clip.w, clip.h), (0,0,0,0))
                    draw = ImageDraw.Draw(img)
                    bbox = draw.textbbox((0,0), text_str, font=font)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]
                    x = (clip.w - tw) // 2
                    y = clip.h - th - 20
                    draw.text((x, y), text_str, fill='yellow', font=font, stroke_width=2, stroke_fill='black')
                    return np.array(img)

                text_clip = VideoClip(make_text_frame, duration=clip.duration)
                clip = CompositeVideoClip([clip, text_clip])
                debug_print(f"Added text overlay to goal_to_net.mp4")

            all_clips.append(clip)

        except Exception as e:
            debug_print(f"Error loading {clip_path}: {e}")

    if not all_clips:
        debug_print("ERROR: No clips could be loaded.")
        return

    # Concatenate all clips
    final_video = concatenate_videoclips(all_clips, method="compose")
    debug_print(f"Total video duration before trimming = {final_video.duration} seconds")

    # Trim video to match audio duration (cut off any extra)
    if final_video.duration > audio_duration:
        final_video = final_video.subclip(0, audio_duration)
        debug_print(f"Trimmed video to {audio_duration} seconds")
    else:
        debug_print("Video is shorter than audio; audio will be truncated.")

    # Set audio
    final_video = final_video.with_audio(audio_clip)

    # Write final video
    final_video.write_videofile(output_path, codec='libx264', audio_codec='aac')
    debug_print(f"Final video saved to {output_path}")

def generate_audio(text, filename):
    url = f"http://api.voicerss.org/?key={VOICERSS_API_KEY}&hl=en-gb&src={text}&f=44khz_16bit_stereo"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with open(filename, 'wb') as f:
            f.write(response.content)
        debug_print(f"Audio saved to {filename}")
    except Exception as e:
        debug_print(f"TTS failed: {e}")
        # Create a silent fallback audio
        os.system(f'ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 2 -q:a 9 -acodec libmp3lame {filename}')

def upload_to_youtube(video_file, title):
    if not YOUTUBE_TOKEN_JSON:
        debug_print("YouTube token missing. Cannot upload.")
        return
    creds_data = json.loads(YOUTUBE_TOKEN_JSON)
    creds = Credentials.from_authorized_user_info(creds_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title,
            "description": f"Latest Premier League result. #PremierLeague #Football",
            "tags": ["PremierLeague", "Football", "Soccer"],
            "categoryId": "17"
        },
        "status": {"privacyStatus": "public"}
    }
    media = MediaFileUpload(video_file, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    try:
        response = request.execute()
        debug_print(f"Upload successful! Video ID: {response['id']}")
    except Exception as e:
        debug_print(f"Upload failed: {e}")

def main():
    try:
        debug_print("DEBUG: main() started")
        fetch_matches()

        # --- TEST MODE: create a dummy video without uploading ---
        debug_print("DEBUG: TEST MODE – generating dummy match video (no upload)")
        fixture_id = 999999
        home = "Arsenal"
        away = "Everton"
        h_score = 2
        a_score = 1
        # Inject a dummy goal
        goals = [{'player': 'Test Scorer', 'minute': 67, 'team': home}]
        debug_print(f"DEBUG: Dummy goal injected: {goals}")
        # Generate audio
        script = generate_script(home, away, h_score, a_score)
        audio_file = f"audio_{fixture_id}.mp3"
        generate_audio(script, audio_file)
        # Build video from clips (no anchor)
        final_video = f"final_{fixture_id}.mp4"
        build_video_from_clips(goals, audio_file, final_video)
        debug_print("TEST MODE: dummy video created, skipping YouTube upload")
        # --- end test mode ---

    except Exception as e:
        debug_print(f"FATAL ERROR: {e}")
        raise

if __name__ == "__main__":
    main()