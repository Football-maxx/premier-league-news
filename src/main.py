#!/usr/bin/env python3
"""
Automated Football Cartoon News System
Supports Premier League, FA Cup, Carabao Cup, Bundesliga, LaLiga.
"""

import os
import sys
import requests
import json
import time
from datetime import datetime
from moviepy import (
    VideoClip,
    AudioFileClip,
    VideoFileClip,
    CompositeVideoClip,
    concatenate_videoclips,
)
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from supabase import create_client, Client
from openai import OpenAI

# ---------- CONFIGURATION (ENVIRONMENT VARIABLES) ----------
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY")
VOICERSS_API_KEY = os.environ.get("VOICERSS_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")
YOUTUBE_TOKEN_JSON = os.environ.get("YOUTUBE_TOKEN_JSON")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
# ------------------------------------------------------------

# Set OpenAI client if key provided
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Competitions to track
COMPETITIONS_TO_TRACK = [
    {"id": "PL", "name": "Premier League"},
    {"id": "FAC", "name": "FA Cup"},
    {"id": "ELC", "name": "Carabao Cup"},
    {"id": "BL1", "name": "Bundesliga"},
    {"id": "PD", "name": "LaLiga"},
]

# Initialize debug log file
DEBUG_LOG = "debug.log"
with open(DEBUG_LOG, "w") as f:
    f.write("Debug log started\n")


def debug_print(msg):
    print(msg)
    sys.stdout.flush()
    with open(DEBUG_LOG, "a") as f:
        f.write(msg + "\n")
        f.flush()


if DRY_RUN:
    debug_print("⚠️ DRY RUN MODE: YouTube upload will be skipped.")

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def fetch_matches():
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    posted_response = (
        supabase.table("matches")
        .select("fixture_id")
        .eq("posted", 1)
        .execute()
    )
    posted_ids = [row["fixture_id"] for row in posted_response.data]

    for comp in COMPETITIONS_TO_TRACK:
        comp_id = comp["id"]
        comp_name = comp["name"]
        debug_print(f"Fetching {comp_name} ({comp_id})")
        url = f"https://api.football-data.org/v4/competitions/{comp_id}/matches"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            time.sleep(6)
        except Exception as e:
            debug_print(f"Error fetching {comp_name}: {e}")
            continue

        for match in data.get("matches", []):
            fixture_id = match["id"]
            home = match["homeTeam"]["name"]
            away = match["awayTeam"]["name"]
            date = match["utcDate"]
            status = match["status"]
            home_score = match["score"]["fullTime"]["home"] or 0
            away_score = match["score"]["fullTime"]["away"] or 0
            season = match.get("season", {}).get("id")

            if fixture_id in posted_ids:
                continue

            data_row = {
                "fixture_id": fixture_id,
                "home_team": home,
                "away_team": away,
                "match_date": date,
                "status": status,
                "home_score": home_score,
                "away_score": away_score,
                "competition": comp_id,
                "season": season,
                "posted": 0,
            }
            supabase.table("matches").upsert(data_row, on_conflict="fixture_id").execute()

            if status == "FINISHED":
                debug_print(f"Processing finished match: {home} vs {away} ({comp_name})")
                process_match(fixture_id, home, away, home_score, away_score)
                supabase.table("matches").update({"posted": 1}).eq("fixture_id", fixture_id).execute()


def get_match_goals(fixture_id):
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
    for goal in data.get("goals", []):
        scorer = goal.get("scorer", {}).get("name")
        if scorer:
            goals.append(
                {
                    "player": scorer,
                    "minute": goal.get("minute"),
                    "team": goal.get("team", {}).get("name"),
                }
            )
    debug_print(f"DEBUG: get_match_goals returning {len(goals)} goals")
    return goals


def generate_script(home, away, h_score, a_score, goals, competition_name):
    script = f"Hello football fans! Welcome to our {competition_name} match recap. "
    script += f"In today's match, {home} faced {away}. "
    if goals:
        script += f"There were {len(goals)} goals in this exciting encounter. "
        for goal in goals:
            script += f"In the {goal['minute']} minute, {goal['player']} scored for {goal['team']}. "
    else:
        script += "It was a goalless draw. "
    script += f"The final score was {home} {h_score} – {away} {a_score}. "
    script += "That's all for now. Don't forget to like and subscribe for more updates!"
    debug_print(f"Generated script: {script}")
    return script


def generate_ai_metadata(home, away, h_score, a_score, goals, competition_name):
    if not openai_client:
        return None
    goals_text = (
        ", ".join([f"{g['player']} ({g['minute']}')" for g in goals])
        if goals
        else "No goals"
    )
    prompt = f"""
    Football match: {home} {h_score} – {a_score} {away} in the {competition_name}.
    Goals: {goals_text}.
    Generate:
    1. A click‑worthy YouTube title (max 70 characters)
    2. 5 relevant tags (comma‑separated)
    3. A short description (max 150 characters) with hashtags.
    Output as JSON: {{"title": "...", "tags": ["...", ...], "description": "..."}}
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        content = response.choices[0].message.content
        import re
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        else:
            return None
    except Exception as e:
        debug_print(f"AI metadata generation failed: {e}")
        return None


def build_video_from_clips(goals, audio_file, output_path):
    debug_print("DEBUG: build_video_from_clips started")
    clip_sequence = [
        "assets/clips/football_news.mp4",
        "assets/clips/football_with_players.mp4",
        "assets/clips/goal_to_net.mp4",
        "assets/clips/celebration.mp4",
    ]
    all_clips = []
    for clip_path in clip_sequence:
        if not os.path.exists(clip_path):
            debug_print(f"WARNING: Clip not found: {clip_path}")
            continue
        try:
            clip = VideoFileClip(clip_path)
            debug_print(f"Loaded {clip_path}, duration={clip.duration}")
            if clip_path == "assets/clips/goal_to_net.mp4" and goals:
                goal = goals[0]
                text_str = f"{goal['player']} – {goal['minute']}'"

                try:
                    font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf", 40
                    )
                except Exception:
                    font = ImageFont.load_default()

                def make_text_frame(t):
                    img = Image.new("RGBA", (clip.w, clip.h), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(img)
                    bbox = draw.textbbox((0, 0), text_str, font=font)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]
                    x = (clip.w - tw) // 2
                    y = clip.h - th - 20
                    draw.text(
                        (x, y),
                        text_str,
                        fill="yellow",
                        font=font,
                        stroke_width=2,
                        stroke_fill="black",
                    )
                    return np.array(img)

                text_clip = VideoClip(make_text_frame, duration=clip.duration)
                clip = CompositeVideoClip([clip, text_clip])
                debug_print("Added text overlay to goal_to_net.mp4")
            all_clips.append(clip)
        except Exception as e:
            debug_print(f"Error loading {clip_path}: {e}")

    if not all_clips:
        debug_print("ERROR: No clips could be loaded.")
        return

    final_video = concatenate_videoclips(all_clips, method="compose")
    video_duration = final_video.duration
    audio_clip = AudioFileClip(audio_file)
    audio_duration = audio_clip.duration
    if audio_duration > video_duration:
        audio_clip = audio_clip.subclipped(0, video_duration)
        debug_print(f"Trimmed audio to {video_duration} seconds")
    final_video = final_video.with_audio(audio_clip)

    # OPTIMIZED WRITE: faster encoding, multi‑thread, no progress bar
    final_video.write_videofile(
        output_path,
        codec="libx264",
        audio_codec="aac",
        preset="ultrafast",
        threads=4,
        logger=None,
        progress_bar=False,
    )
    debug_print(f"Final video saved to {output_path}")


def generate_audio(text, filename):
    url = (
        f"http://api.voicerss.org/?key={VOICERSS_API_KEY}"
        f"&hl=en-gb&src={text}&f=44khz_16bit_stereo"
    )
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with open(filename, "wb") as f:
            f.write(response.content)
        debug_print(f"Audio saved to {filename}")
    except Exception as e:
        debug_print(f"TTS failed: {e}")
        os.system(
            f"ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 2 "
            f"-q:a 9 -acodec libmp3lame {filename}"
        )


def process_match(fixture_id, home, away, h_score, a_score):
    debug_print(f"DEBUG: process_match called with fixture_id={fixture_id}")
    result = (
        supabase.table("matches")
        .select("competition")
        .eq("fixture_id", fixture_id)
        .execute()
    )
    comp_code = result.data[0]["competition"] if result.data else "PL"
    comp_name = {
        "PL": "Premier League",
        "FAC": "FA Cup",
        "ELC": "Carabao Cup",
        "BL1": "Bundesliga",
        "PD": "LaLiga",
    }.get(comp_code, "Football")

    goals = get_match_goals(fixture_id)
    script = generate_script(home, away, h_score, a_score, goals, comp_name)
    audio_file = f"audio_{fixture_id}.mp3"
    generate_audio(script, audio_file)
    final_video = f"final_{fixture_id}.mp4"
    build_video_from_clips(goals, audio_file, final_video)

    ai_meta = generate_ai_metadata(home, away, h_score, a_score, goals, comp_name)
    if ai_meta:
        title = ai_meta.get(
            "title", f"{comp_name} Result: {home} {h_score} – {a_score} {away}"
        )[:70]
        tags = ai_meta.get("tags", ["Football", "Highlights"])
        description = ai_meta.get(
            "description", "Watch the full match recap with cartoon animation! ⚽🎬"
        )
    else:
        title = (
            f"{comp_name} Result: {home} {h_score} – {a_score} {away} "
            f"- {datetime.now().strftime('%Y%m%d-%H%M')}"
        )
        tags = ["Football", "Soccer", "Highlights", "Match Recap", comp_name]
        description = (
            f"Watch the full match recap with cartoon animation! ⚽🎬\n\n"
            f"#{comp_name.replace(' ', '')} #Highlights #Football"
        )

    if DRY_RUN:
        debug_print(f"DRY RUN: Video would be uploaded as '{title}'")
    else:
        upload_to_youtube(final_video, title, description, tags)


def upload_to_youtube(video_file, title, description, tags):
    if not YOUTUBE_TOKEN_JSON:
        debug_print("YouTube token missing. Cannot upload.")
        return
    creds_data = json.loads(YOUTUBE_TOKEN_JSON)
    # The JSON may come from either OAuth Playground or Python script.
    if "client_id" not in creds_data:
        # Playground format: use the token directly with refresh
        # Note: we already imported Credentials at top
        creds = Credentials(
            token=creds_data.get("access_token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ.get("YOUTUBE_CLIENT_ID", ""),
            client_secret=os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
        )
    else:
        creds = Credentials.from_authorized_user_info(creds_data)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "17",
        },
        "status": {"privacyStatus": "public"},
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
        if DRY_RUN:
            debug_print("DRY RUN is active – no YouTube uploads will occur.")
        fetch_matches()
    except Exception as e:
        debug_print(f"FATAL ERROR: {e}")
        raise


if __name__ == "__main__":
    main()
