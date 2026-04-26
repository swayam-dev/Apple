import re
import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

TOKEN = os.environ.get("APPLE_MUSIC_TOKEN", "")
TOKEN = "Bearer " + TOKEN


def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Origin": "https://music.apple.com",
        "Authorization": TOKEN,
    }


def parse_playlist_url(url: str) -> tuple:
    url_clean = url.split("?")[0]
    match = re.search(
        r'music\.apple\.com/([a-z]{2})/playlist/(?:[^/?]+/)?(pl\.[A-Za-z0-9\-_]+)',
        url_clean
    )
    if match:
        return match.group(1), match.group(2)
    raise ValueError(f"Could not parse URL. Expected: https://music.apple.com/us/playlist/name/pl.XXXX")


def get_playlist(storefront: str, playlist_id: str) -> tuple:
    headers = get_headers()
    url = f"https://api.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}"
    resp = requests.get(url, params={"include": "tracks"}, headers=headers, timeout=15)

    if resp.status_code == 401:
        raise PermissionError("Token is invalid or expired.")
    if resp.status_code == 404:
        raise LookupError("Playlist not found. Make sure it's public and the URL is correct.")
    resp.raise_for_status()

    data = resp.json()
    playlist_name = data["data"][0]["attributes"].get("name", "Unknown Playlist")

    tracks = []
    tracks_rel = data["data"][0].get("relationships", {}).get("tracks", {})
    next_url = tracks_rel.get("next")

    def extract(items):
        for item in items:
            a = item.get("attributes", {})
            tracks.append({
                "name": a.get("name", "Unknown"),
                "artist": a.get("artistName", "Unknown")
            })

    extract(tracks_rel.get("data", []))
    while next_url:
        page = requests.get(f"https://api.music.apple.com{next_url}", headers=headers, timeout=15)
        page.raise_for_status()
        pd = page.json()
        extract(pd.get("data", []))
        next_url = pd.get("next")

    return playlist_name, tracks


def format_tracklist(playlist_name: str, tracks: list) -> str:
    parts = []
    for t in tracks:
        song = t["name"]
        artist = t["artist"]
        # Remove parenthetical features e.g. "(feat. X)"
        artist = re.sub(r'\s*\(.*?\)', '', artist).strip()
        # Take only the first artist before feat./ft./&/,/x
        artist = re.split(r'\s*(?:feat\.|ft\.|&|,| x )\s*', artist, flags=re.IGNORECASE)[0].strip()
        parts.append(f"{song} {artist}")
    return " || ".join(parts)


@app.route("/playlist", methods=["GET", "POST"])
def playlist():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        url = body.get("url", "").strip()
    else:
        url = request.args.get("url", "").strip()

    if not url:
        return jsonify({"error": "Missing 'url' parameter"}), 400

    if not TOKEN:
        return jsonify({"error": "Server not configured: APPLE_MUSIC_TOKEN env var is missing"}), 500

    try:
        storefront, playlist_id = parse_playlist_url(url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        playlist_name, tracks = get_playlist(storefront, playlist_id)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Upstream error: {str(e)}"}), 502

    text = format_tracklist(playlist_name, tracks)

    return jsonify({
        "playlist": playlist_name,
        "text": text
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
