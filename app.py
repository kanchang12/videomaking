from flask import Flask, render_template, request, jsonify, send_file
import os
import uuid
import google.generativeai as genai
from google.cloud import texttospeech
from threading import Thread
import time
import subprocess
import json

app = Flask(__name__)

# Configure Gemini API
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))

# In-memory storage for jobs
jobs = {}

def generate_script(product_name, details, tone, audience, cta):
    """Generate video script using Gemini API"""
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    Create a compelling 30-second video advertisement script for:
    
    Product: {product_name}
    Details: {details}
    Tone: {tone}
    Audience: {audience}
    Call to Action: {cta}
    
    Requirements:
    - 60-80 words maximum for 30-second video
    - Engaging and persuasive
    - Natural flow for voiceover
    - {tone} tone throughout
    - End with the call to action
    
    Return only the script text, no formatting.
    """
    
    response = model.generate_content(prompt)
    return response.text.strip()

def generate_audio(text, output_path):
    """Generate audio using Google Cloud TTS"""
    client = texttospeech.TextToSpeechClient()
    
    synthesis_input = texttospeech.SynthesisInput(text=text)
    
    voice = texttospeech.VoiceSelectionParams(
        language_code="en-US",
        name="en-US-Standard-J",
        ssml_gender=texttospeech.SsmlVoiceGender.FEMALE
    )
    
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )
    
    response = client.synthesize_speech(
        input=synthesis_input, 
        voice=voice, 
        audio_config=audio_config
    )
    
    with open(output_path, "wb") as out:
        out.write(response.audio_content)
    
    return output_path

def create_video(script, audio_path, output_path, product_name):
    """Create video using ffmpeg directly"""
    try:
        # Get audio duration
        duration_cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries', 
            'format=duration', '-of', 'csv=p=0', audio_path
        ]
        result = subprocess.run(duration_cmd, capture_output=True, text=True)
        duration = float(result.stdout.strip())
        
        # Clean text for ffmpeg
        clean_product = product_name.replace("'", "").replace(":", "")
        clean_script = script.replace("'", "").replace(":", "")[:80]
        
        # Create video with ffmpeg
        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', f'color=c=0x003366:size=1280x720:duration={duration}',
            '-i', audio_path,
            '-vf', f'drawtext=text={clean_product}:fontsize=60:fontcolor=white:x=(w-text_w)/2:y=100,'
                   f'drawtext=text={clean_script}:fontsize=30:fontcolor=white:x=50:y=300:enable=between(t\\,3\\,{duration})',
            '-c:v', 'libx264', '-c:a', 'aac', '-shortest',
            output_path
        ]
        
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
        return True
        
    except Exception as e:
        print(f"Video creation error: {e}")
        # Ultra simple fallback
        try:
            simple_cmd = [
                'ffmpeg', '-y',
                '-f', 'lavfi', '-i', f'color=c=blue:size=1280x720:duration=10',
                '-i', audio_path,
                '-c:v', 'libx264', '-c:a', 'aac', '-shortest',
                output_path
            ]
            subprocess.run(simple_cmd, check=True)
            return True
        except:
            return False

def process_video_generation(job_id, product_name, details, tone, audience, cta):
    """Background task to generate video"""
    try:
        jobs[job_id]['status'] = 'generating_script'
        
        # Generate script
        script = generate_script(product_name, details, tone, audience, cta)
        jobs[job_id]['script'] = script
        
        jobs[job_id]['status'] = 'generating_audio'
        
        # Generate audio
        audio_path = f"temp_audio_{job_id}.mp3"
        generate_audio(script, audio_path)
        
        jobs[job_id]['status'] = 'creating_video'
        
        # Create video
        video_path = f"temp_video_{job_id}.mp4"
        create_video(script, audio_path, video_path, product_name)
        
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['video_path'] = video_path
        
        # Clean up audio file
        os.remove(audio_path)
        
    except Exception as e:
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error'] = str(e)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate_video():
    data = request.json
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'status': 'starting',
        'created_at': time.time()
    }
    
    thread = Thread(target=process_video_generation, args=(
        job_id,
        data['product_name'],
        data['details'],
        data['tone'],
        data['audience'],
        data['cta']
    ))
    thread.daemon = True
    thread.start()
    
    return jsonify({'job_id': job_id})

@app.route('/status/<job_id>')
def get_status(job_id):
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify(jobs[job_id])

@app.route('/download/<job_id>')
def download_video(job_id):
    if job_id not in jobs or jobs[job_id]['status'] != 'completed':
        return jsonify({'error': 'Video not ready'}), 404
    
    video_path = jobs[job_id]['video_path']
    return send_file(video_path, as_attachment=True, download_name=f'advertisement_{job_id}.mp4')

if __name__ == '__main__':
    app.run(debug=True, port=5000)