import os
import json
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv # Import load_dotenv

# Load environment variables from .env file
load_dotenv() 

app = Flask(__name__, static_folder='.', static_url_path='') # Set static folder to current directory for index.html
CORS(app) # Enable CORS for communication between frontend and backend

# --- Configuration ---
# Now, these will be loaded from the .env file if it exists
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


if not GOOGLE_API_KEY:
    print("CRITICAL: GOOGLE_API_KEY environment variable not set.")
    print("Please set it in your .env file or as an environment variable directly.")
    print("You can get one from https://makersuite.google.com/app/apikey")
    exit(1) 

if not YOUTUBE_API_KEY:
    print("CRITICAL: YOUTUBE_API_KEY environment variable not set.")
    print("Please set it in your .env file or as an environment variable directly.")
    print("You can get one from console.cloud.google.com, APIs & Services -> Credentials.")
    print("Also, ensure YouTube Data API v3 is enabled in your GCP project.")
    exit(1)


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


# --- Helper Functions for API Calls ---
def call_gemini_api(prompt, response_schema=None):
    """
    Makes a call to the Gemini API with the given prompt and optional response schema.
    """
    headers = {
        'Content-Type': 'application/json'
    }
    params = {
        'key': GOOGLE_API_KEY
    }

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}]
    }

    if response_schema:
        payload["generationConfig"] = {
            "responseMimeType": "application/json",
            "responseSchema": response_schema
        }
    
    try:
        response = requests.post(GEMINI_API_URL, headers=headers, params=params, json=payload, timeout=30) # Added timeout
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        result = response.json()

        if result.get("candidates") and result["candidates"][0].get("content") and result["candidates"][0]["content"].get("parts"):
            text_response = result["candidates"][0]["content"]["parts"][0]["text"]
            
            # Attempt to parse JSON if a schema was provided
            if response_schema:
                # Remove markdown fences if present
                clean_json_string = text_response.replace('```json\n', '').replace('\n```', '').strip()
                return json.loads(clean_json_string)
            return text_response.strip() # Strip whitespace from plain text responses
        else:
            app.logger.error(f"Unexpected Gemini API response structure: {result}")
            return None
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Error calling Gemini API: {e}")
        return None
    except json.JSONDecodeError as e:
        app.logger.error(f"Error parsing Gemini API JSON response: {e}, Raw response: {text_response}")
        return None

def search_youtube_videos(query):
    """
    Searches YouTube for videos related to the query using the YouTube Data API.
    Returns the video ID and title of the top relevant result, or None if not found.
    Now fetches multiple results and tries to pick a suitable one.
    """
    params = {
        'key': YOUTUBE_API_KEY,
        'q': query,
        'part': 'snippet',
        'type': 'video',
        'maxResults': 5, # Fetch more results
        'videoEmbeddable': 'true' # Filter for embeddable videos
    }
    
    try:
        response = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=10) # Added timeout
        response.raise_for_status()
        results = response.json()

        if results and results.get('items'):
            # Try to find the most relevant/embeddable video from the top results
            # This is a heuristic, full embeddability can only be guaranteed by trying.
            for item in results['items']:
                video_id = item['id']['videoId']
                video_title = item['snippet']['title']
                
                # Simple heuristic: prioritize videos that look like lectures or tutorials
                # You can expand this logic based on channel names, description keywords, etc.
                if "lecture" in video_title.lower() or "tutorial" in video_title.lower() or "course" in video_title.lower():
                    return {"title": video_title, "url": f"https://www.youtube.com/watch?v={video_id}"}
            
            # If no specific lecture/tutorial found, return the first embeddable one
            first_item = results['items'][0]
            video_id = first_item['id']['videoId']
            video_title = first_item['snippet']['title']
            return {"title": video_title, "url": f"https://www.youtube.com/watch?v={video_id}"}

        return None # No videos found
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Error calling YouTube Data API: {e}")
        return None
    except Exception as e:
        app.logger.error(f"An unexpected error occurred during YouTube search: {e}")
        return None


# --- Flask Routes ---

@app.route('/')
def serve_index():
    """Serves the index.html file for the root URL."""
    return send_from_directory(app.static_folder, 'index.html')

# This route serves other static files (like your JavaScript modules, if they were separate files)
# Flask automatically handles static files in the static_folder, but this route makes it explicit
# for the root path. For files other than index.html, they would be accessed directly, e.g., /styles.css
@app.route('/<path:filename>')
def serve_static(filename):
    """Serves other static files from the static folder."""
    return send_from_directory(app.static_folder, filename)


@app.route('/extract_title', methods=['POST'])
def extract_title_route():
    data = request.get_json()
    syllabus_text = data.get('syllabus_text', '')
    
    if not syllabus_text:
        return jsonify({"error": "Syllabus text is required"}), 400

    prompt = f"""From the following syllabus text, identify and extract the main course title or syllabus title. Respond with only the title string. If no clear title is found, respond with "Unknown Course".

Syllabus:
{syllabus_text}
"""
    
    title = call_gemini_api(prompt)
    return jsonify({"title": title})

@app.route('/extract_topics', methods=['POST'])
def extract_topics_route():
    data = request.get_json()
    syllabus_text = data.get('syllabus_text', '')

    if not syllabus_text:
        return jsonify({"error": "Syllabus text is required"}), 400

    prompt = f"""Extract key academic topics or subjects from the following syllabus text. Focus on main, distinct topics that someone would learn about. Return them as a JSON array of objects, where each object has a single key 'topic'. Do not include introductory phrases like 'introduction to' or 'basics of' unless the topic specifically requires it for clarity.
                
Syllabus:
{syllabus_text}
"""
    
    response_schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "topic": { "type": "STRING" }
            },
            "propertyOrdering": ["topic"]
        }
    }
    
    topics_raw = call_gemini_api(prompt, response_schema)
    topics = [item.get('topic') for item in topics_raw if item.get('topic')] if topics_raw else []
    
    return jsonify({"topics": topics})

@app.route('/suggest_video', methods=['POST'])
def suggest_video_route():
    data = request.get_json()
    topic = data.get('topic', '')

    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    # Prioritize searching YouTube Data API for actual videos
    # Improved query with more specific terms
    search_query = f"{topic} academic lecture full course tutorial explanation"
    video_info = search_youtube_videos(search_query)

    if video_info:
        return jsonify({"title": video_info["title"], "url": video_info["url"]})
    else:
        # Fallback to Gemini if YouTube Data API search fails or returns no results
        app.logger.warning(f"YouTube Data API failed to find a suitable video for '{topic}'. Falling back to Gemini.")
        
        prompt = f"""For the academic topic "{topic}", suggest a plausible YouTube video title and a realistic YouTube URL. The URL should follow the format 'https://www.youtube.com/watch?v=xxxxxxxxxxx' where 'xxxxxxxxxxx' is a valid-looking YouTube video ID (e.g., 11 characters, alphanumeric). Prioritize topics that are likely to have educational content. Ensure the video title is concise and directly related to the topic. Return the response as a JSON object with 'title' and 'url' keys.

Example:
{{
  "title": "Introduction to Python Programming Tutorial",
  "url": "https://www.youtube.com/watch?v=rfscVS0vtbw"
}}

Topic: {topic}
"""
        response_schema = {
            "type": "OBJECT",
            "properties": {
                "title": { "type": "STRING" },
                "url": { "type": "STRING" }
            },
            "propertyOrdering": ["title", "url"]
        }
        generated_video_info = call_gemini_api(prompt, response_schema)
        
        if generated_video_info:
            return jsonify({"title": generated_video_info.get("title"), "url": generated_video_info.get("url")})
        
        return jsonify({"title": "Failed to suggest video", "url": "#"})

@app.route('/generate_notes', methods=['POST'])
def generate_notes_route():
    data = request.get_json()
    topic = data.get('topic', '')
    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    # Updated prompt to explicitly ask for Markdown formatting with headings and clear spacing
    prompt = f"""Provide concise, well-structured, and comprehensive notes for the academic topic: "{topic}".
    Format the notes using Markdown. Ensure clear headings and subheadings, use bullet points for lists,
    and include blank lines between paragraphs and sections for excellent readability and proper spacing.
    Focus on essential concepts, definitions, and important facts.
    """
    notes = call_gemini_api(prompt)
    return jsonify({"notes": notes if notes else "Could not generate notes for this topic."})

@app.route('/generate_flashcards', methods=['POST'])
def generate_flashcards_route():
    data = request.get_json()
    topic = data.get('topic', '')
    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    prompt = f"""Generate 5-7 distinct flashcards (question/answer pairs) for the academic topic: "{topic}".
    Each flashcard should be an object with a 'front' (question) and 'back' (answer) key.
    Return them as a JSON array of objects.

    Example format:
    [
      {{ "front": "What is Python?", "back": "A high-level, interpreted programming language." }},
      {{ "front": "Key features of Python?", "back": "Readability, extensive libraries, dynamic typing, etc." }}
    ]
    """
    response_schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "front": {"type": "STRING"},
                "back": {"type": "STRING"}
            },
            "propertyOrdering": ["front", "back"]
        }
    }
    flashcards = call_gemini_api(prompt, response_schema)
    return jsonify({"flashcards": flashcards if flashcards else []})

@app.route('/generate_questions', methods=['POST'])
def generate_questions_route():
    data = request.get_json()
    topic = data.get('topic', '')
    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    prompt = f"""Generate 3-5 important subjective (open-ended) questions for the academic topic: "{topic}".
    These questions should encourage critical thinking and deeper understanding.
    Return them as a JSON array of strings.

    Example format:
    [
      "Discuss the implications of X on Y.",
      "Compare and contrast A and B, providing relevant examples."
    ]
    """
    response_schema = {
        "type": "ARRAY",
        "items": {
            "type": "STRING"
        }
    }
    questions = call_gemini_api(prompt, response_schema)
    return jsonify({"questions": questions if questions else []})


if __name__ == '__main__':
    app.run(debug=True) # debug=True enables auto-reloading and better error messages
