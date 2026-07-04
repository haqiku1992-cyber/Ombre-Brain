from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("VOICE_ID")

@app.route("/generate-voice", methods=["POST"])
def generate_voice():
    data = request.json
    text = data.get("text")
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2"
    }
    
    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code == 200:
        return response.content, 200, {"Content-Type": "audio/mpeg"}
    else:
        return jsonify({"error": "Failed to generate voice"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
