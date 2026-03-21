#!/usr/bin/env python3
"""
Automated Premier League Cartoon News System
Runs on GitHub Actions (free)
"""

import os
import requests
import json
import base64
import time
from datetime import datetime
from moviepy import VideoClip, AudioFileClip, VideoFileClip, CompositeVideoClip, TextClip, concatenate_videoclips
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
        print(f"Error fetching matches: {e}")
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
            print(f"Processing finished match: {home} vs {away}")
            process_match(fixture_id, home, away, home_score, away_score)
            # Mark as posted
            supabase.table("matches").update({"posted": 1}).eq("fixture_id", fixture_id).execute()

def get_match_goals(fixture_id):
    """Get goals from Football-Data.org for a specific match."""
    url = f"https://api.football-data.org/v4/matches/{fixture_id}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Could not fetch match details: {e}")
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
    return goals

def process_match(fixture_id, home, away, h_score, a_score):
    """Generate video for a single match, including goal clips."""
    # 1. Generate anchor video (news report)
    script = generate_script(home, away, h_score, a_score)
    audio_file = f"audio_{fixture_id}.mp3"
    generate_audio(script, audio_file)
    mouth_cues = get_mouth_cues(audio_file)
    anchor_video = f"anchor_{fixture_id}.mp4"
    create_video(audio_file, mouth_cues, home, away, h_score, a_score, anchor_video)

    # 2. Get goals for this match
    goals = get_match_goals(fixture_id)
    # --- TEMPORARY: inject dummy goal for testing ---
    if fixture_id == 999999 and not goals:
        goals = [{'player': 'Test Scorer', 'minute': 67, 'team': home}]
        print("Dummy goal added for testing.")
    # --- end temporary code ---

    # DEBUG: print goals to log
    print(f"DEBUG: Number of goals = {len(goals)}")
    for g in goals:
        print(f"DEBUG: Goal: {g}")

    # 3. Combine anchor video with goal clips
    final_video = f"final_{fixture_id}.mp4"
    combine_anchor_with_goals(anchor_video, goals, final_video)

    # 4. Upload
    title = f"Premier League Result: {home} {h_score} – {a_score} {away}"
    upload_to_youtube(final_video, title)

    # Clean up temp files (optional)
    # os.remove(anchor_video)
    # os.remove(audio_file)
    # os.remove(final_video)

def generate_script(home, away, h_score, a_score):
    return f"Hello football fans! Here's the latest Premier League result. {home} {h_score} – {a_score} {away}. That's all for now. Don't forget to like and subscribe!"

def combine_anchor_with_goals(anchor_path, goals, output_path):
    """Concatenate anchor video with goal clips, adding text overlays."""
    anchor = VideoFileClip(anchor_path)
    clips = [anchor]

    # List of your cartoon clips – ensure these files exist in assets/clips/
    goal_sequence = [
        "assets/clips/goal_to_net.mp4",
        "assets/clips/football_with_players.mp4",
        "assets/clips/celebration.mp4",
        "assets/clips/football_news.mp4"
    ]

    for goal in goals:
        for clip_path in goal_sequence:
            try:
                clip = VideoFileClip(clip_path)
                # Add text overlay with scorer name and minute
                txt = TextClip(f"{goal['player']} – {goal['minute']}'",
                               fontsize=40, color='yellow', stroke_color='black', stroke_width=2,
                               font='Arial', method='caption')
                txt = txt.set_position(('center', 'bottom')).set_duration(clip.duration)
                combined = CompositeVideoClip([clip, txt])
                clips.append(combined)
            except Exception as e:
                print(f"Could not add clip {clip_path}: {e}")

    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(output_path, codec='libx264', audio_codec='aac')
    print(f"Final video saved to {output_path}")

def generate_audio(text, filename):
    url = f"http://api.voicerss.org/?key={VOICERSS_API_KEY}&hl=en-gb&src={text}&f=44khz_16bit_stereo"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with open(filename, 'wb') as f:
            f.write(response.content)
        print(f"Audio saved to {filename}")
    except Exception as e:
        print(f"TTS failed: {e}")
        # Create a silent fallback audio
        os.system(f'ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 2 -q:a 9 -acodec libmp3lame {filename}')

def get_mouth_cues(audio_file):
    print("Using default mouth shapes (Replicate call skipped).")
    return [{"start": 0.0, "end": 999.0, "value": "X"}]

def create_video(audio_file, mouth_cues, home, away, h_score, a_score, output_file):
    background = Image.open("assets/background.png").convert("RGBA")
    base_char = Image.open("assets/base_character.png").convert("RGBA")
    mouths = {}
    for shape in "ABCDEFGHX":
        try:
            mouths[shape] = Image.open(f"assets/mouths/{shape}.png").convert("RGBA")
        except:
            mouths[shape] = Image.open("assets/mouths/X.png").convert("RGBA")

    fps = 24
    audio_clip = AudioFileClip(audio_file)
    duration = audio_clip.duration
    char_base_pos = (100, 150)  # adjust based on your assets
    mouth_offset = (50, 80)     # adjust based on your assets

    def make_frame(t):
        frame = background.copy()
        bounce = 5 * np.sin(2 * np.pi * 1.5 * t)
        char_pos = (int(char_base_pos[0]), int(char_base_pos[1] + bounce))
        frame.paste(base_char, char_pos, base_char)

        shape = "X"
        for cue in mouth_cues:
            if cue["start"] <= t <= cue["end"]:
                shape = cue["value"]
                break
        mouth_img = mouths.get(shape, mouths["X"])
        mouth_pos = (int(char_pos[0] + mouth_offset[0]), int(char_pos[1] + mouth_offset[1]))
        frame.paste(mouth_img, mouth_pos, mouth_img)

        draw = ImageDraw.Draw(frame)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf", 40)
        except:
            font = ImageFont.load_default()
        text = f"{home} {h_score} – {a_score} {away}"
        draw.text((502, 302), text, fill="black", font=font)
        draw.text((500, 300), text, fill="yellow", font=font)
        return np.array(frame)

    video = VideoClip(make_frame, duration=duration)
    video = video.with_audio(audio_clip)
    video.write_videofile(output_file, fps=fps, codec="libx264", audio_codec="aac")
    print(f"Anchor video saved to {output_file}")

def upload_to_youtube(video_file, title):
    if not YOUTUBE_TOKEN_JSON:
        print("YouTube token missing. Cannot upload.")
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
        print(f"Upload successful! Video ID: {response['id']}")
    except Exception as e:
        print(f"Upload failed: {e}")

def main():
    fetch_matches()
    # Test with dummy match – forces a video to be created
    process_match(999999, "Arsenal", "Everton", 2, 1)

if __name__ == "__main__":
    main()