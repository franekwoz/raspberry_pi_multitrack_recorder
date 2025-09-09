from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import subprocess
import signal
from threading import Lock
import wave
import contextlib
import tempfile
import uuid

app = Flask(__name__)
app.config['RECORDINGS_DIR'] = os.path.join(os.getcwd(), 'recordings')
if not os.path.exists(app.config['RECORDINGS_DIR']):
    os.makedirs(app.config['RECORDINGS_DIR'])

# Store process info
task = {
    'process': None,
    'mode': None,  # 'record' or 'play'
    'temp_file': None,  # temporary file for seeking
}
lock = Lock()

# Utility: list recordings
def list_recordings():
    files = [f for f in os.listdir(app.config['RECORDINGS_DIR']) if f.endswith('.wav')]
    files.sort()
    return files

@app.route('/')
def index():
    recordings = list_recordings()
    return render_template('index.html', recordings=recordings)

@app.route('/record', methods=['POST'])
def start_record():
    with lock:
        # Stop any existing process before starting record
        if task['process']:
            proc = task['process']
            proc.send_signal(signal.SIGINT)
            proc.wait()
            task['process'] = None
            task['mode'] = None
        filename = request.json.get('filename') or ''
        device = request.json.get('device', 'xr18')  # Default to xr18 if not specified
        
        if not filename:
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"rec_{timestamp}.wav"
        else:
            # Add .wav if no extension is provided
            if '.' not in filename:
                filename += '.wav'
        filepath = os.path.join(app.config['RECORDINGS_DIR'], filename)
        
        # Choose command based on device selection
        if device == 'xr18':
            cmd = [
                'arecord',
                '-D', 'hw:3,0',
                '-f', 'S32_LE',
                '-c', '18',
                '-r', '48000',
                filepath
            ]
        elif device == 'x32':
            cmd = [
                'arecord',
                '-D', 'hw:3,0',
                '-f', 'S32_LE',
                '-c', '32',
                '-r', '48000',
                filepath
            ]
        else:
            return jsonify(status='error', message='Invalid device selection'), 400
            
        proc = subprocess.Popen(cmd, shell=False)
        task['process'] = proc
        task['mode'] = 'record'
        return jsonify(status='recording', file=filename)

@app.route('/pause', methods=['POST'])
def pause_record():
    with lock:
        proc = task.get('process')
        if not proc or task.get('mode') != 'record':
            return jsonify(status='error', message='Not recording'), 400
        # SIGSTOP to pause
        proc.send_signal(signal.SIGSTOP)
        task['mode'] = 'paused'
        return jsonify(status='paused')

@app.route('/resume', methods=['POST'])
def resume_record():
    with lock:
        proc = task.get('process')
        if not proc or task.get('mode') != 'paused':
            return jsonify(status='error', message='Not paused'), 400
        proc.send_signal(signal.SIGCONT)
        task['mode'] = 'record'
        return jsonify(status='recording')

@app.route('/stop', methods=['POST'])
def stop_task():
    with lock:
        proc = task.get('process')
        if not proc:
            return jsonify(status='error', message='Nothing to stop'), 400
        proc.send_signal(signal.SIGINT)
        proc.wait()
        mode = task['mode']
        
        # Clean up temporary file if it exists
        temp_file = task.get('temp_file')
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass
        
        task['process'] = None
        task['mode'] = None
        task['temp_file'] = None
        return jsonify(status='stopped', mode=mode)

@app.route('/play', methods=['POST'])
def start_play():
    with lock:
        # Stop any existing process before starting play
        if task['process']:
            proc = task['process']
            proc.send_signal(signal.SIGINT)
            proc.wait()
            task['process'] = None
            task['mode'] = None
        filename = request.json.get('filename')
        device = request.json.get('device', 'xr18')  # Default to xr18 if not specified
        
        if not filename:
            return jsonify(status='error', message='No file selected'), 400
        filepath = os.path.join(app.config['RECORDINGS_DIR'], filename)
        if not os.path.exists(filepath):
            return jsonify(status='error', message='File not found'), 404
            
        # Choose command based on device selection
        if device == 'xr18':
            cmd = ['aplay', '-D', 'hw:3,0', filepath]
        elif device == 'x32':
            cmd = ['aplay', '-D', 'hw:XUSB,0', '-c', '32', '-r', '48000', '-f', 'S32_LE', filepath]
        else:
            return jsonify(status='error', message='Invalid device selection'), 400
            
        proc = subprocess.Popen(cmd)
        task['process'] = proc
        task['mode'] = 'play'
        return jsonify(status='playing', file=filename)

@app.route('/next', methods=['POST'])
def next_file():
    recordings = list_recordings()
    current = request.json.get('current')
    if current not in recordings:
        return jsonify(status='error', message='Invalid current file'), 400
    idx = recordings.index(current)
    nxt = recordings[(idx + 1) % len(recordings)] if recordings else None
    return jsonify(status='ok', next=nxt)

@app.route('/rewind', methods=['POST'])
def rewind():
    # Stop and re-play from start
    with lock:
        # implement by stopping and starting same file
        current = request.json.get('current')
        # reuse stop and play
        stop_task()
        request.json['filename'] = current
        return start_play()

@app.route('/recordings/<path:filename>')
def download_recording(filename):
    # Serve .wav files from the recordings directory
    return send_from_directory(app.config['RECORDINGS_DIR'], filename)

@app.route('/duration/<filename>')
def get_duration(filename):
    filepath = os.path.join(app.config['RECORDINGS_DIR'], filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    try:
        with contextlib.closing(wave.open(filepath, 'rb')) as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration = frames / float(rate)
        return jsonify({'duration': duration})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/seek', methods=['POST'])
def seek_position():
    with lock:
        proc = task.get('process')
        if not proc or task.get('mode') != 'play':
            return jsonify(status='error', message='Not playing'), 400
        
        filename = request.json.get('filename')
        position = request.json.get('position', 0)
        device = request.json.get('device', 'xr18')
        
        if not filename:
            return jsonify(status='error', message='No file specified'), 400
            
        filepath = os.path.join(app.config['RECORDINGS_DIR'], filename)
        if not os.path.exists(filepath):
            return jsonify(status='error', message='File not found'), 404
        
        # Stop current playback
        proc.send_signal(signal.SIGINT)
        proc.wait()
        
        # Calculate seek position in bytes (approximate)
        try:
            with contextlib.closing(wave.open(filepath, 'rb')) as wf:
                rate = wf.getframerate()
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                # Calculate bytes per second
                bytes_per_second = rate * channels * sample_width
                seek_bytes = int(position * bytes_per_second)
        except Exception as e:
            return jsonify(status='error', message=f'Error calculating seek position: {str(e)}'), 500
        
        # Use sox with temporary file for seeking
        
        temp_filename = f"seek_{uuid.uuid4().hex[:8]}.wav"
        temp_filepath = os.path.join('/tmp', temp_filename)
        
        try:
            if device == 'xr18':
                # Use sox to create trimmed file, then play it
                cmd = ['bash', '-c', f'sox "{filepath}" "{temp_filepath}" trim {position} && aplay -D hw:3,0 "{temp_filepath}" && rm -f "{temp_filepath}"']
            elif device == 'x32':
                # Use sox to create trimmed file, then play it with format
                cmd = ['bash', '-c', f'sox "{filepath}" "{temp_filepath}" trim {position} && aplay -D hw:XUSB,0 -c 32 -r 48000 -f S32_LE "{temp_filepath}" && rm -f "{temp_filepath}"']
            else:
                return jsonify(status='error', message='Invalid device selection'), 400
        except Exception as e:
            # Fallback: restart from beginning if sox fails
            if device == 'xr18':
                cmd = ['aplay', '-D', 'hw:3,0', filepath]
            elif device == 'x32':
                cmd = ['aplay', '-D', 'hw:XUSB,0', '-c', '32', '-r', '48000', '-f', 'S32_LE', filepath]
            else:
                return jsonify(status='error', message='Invalid device selection'), 400
        
        try:
            proc = subprocess.Popen(cmd, shell=False)
            task['process'] = proc
            task['mode'] = 'play'
            
            # Store temp file path for cleanup
            task['temp_file'] = temp_filepath
            
            return jsonify(status='seeking', position=position, file=filename)
        except Exception as e:
            # Clean up temp file if process creation fails
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except:
                    pass
            return jsonify(status='error', message=f'Seek failed: {str(e)}'), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
