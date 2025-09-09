from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import subprocess
import signal
from threading import Lock
import wave
import contextlib
import tempfile
import uuid
import time

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

# Helper: aggressively release audio device if lingering aplay processes exist
def force_release_device(selected_device: str) -> None:
    try:
        if selected_device == 'xr18':
            pattern = 'hw:3,0'
        elif selected_device == 'x32':
            pattern = 'hw:XUSB,0'
        else:
            return
        # Kill any aplay using the target device
        subprocess.run(['bash', '-c', f'pkill -f "aplay -D {pattern}" || true'], check=False)
        # Small wait to allow kernel to release the device nodes
        time.sleep(0.2)
    except Exception:
        # Best-effort; ignore errors
        pass

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
        
        filename = request.json.get('filename')
        position = request.json.get('position', 0)
        device = request.json.get('device', 'xr18')
        
        if not filename:
            return jsonify(status='error', message='No file specified'), 400
            
        filepath = os.path.join(app.config['RECORDINGS_DIR'], filename)
        if not os.path.exists(filepath):
            return jsonify(status='error', message='File not found'), 404
        
        # If something is playing, stop it first
        if proc:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            except:
                pass
        
        # Small delay to ensure audio device is released
        time.sleep(0.15)
        # Proactively kill any stray aplay still holding the device
        force_release_device(device)
        
        # Use sox piping to aplay to avoid temp files and reduce latency
        try:
            if device == 'xr18':
                # Pipe trimmed audio directly into aplay on XR18
                cmd = ['bash', '-c', f'sox -V1 -q "{filepath}" -t wav - trim {position} | aplay -D hw:3,0 -']
            elif device == 'x32':
                # Pipe trimmed audio directly into aplay with explicit format for X32
                cmd = ['bash', '-c', f'sox -V1 -q "{filepath}" -t wav - trim {position} | aplay -D hw:XUSB,0 -c 32 -r 48000 -f S32_LE -']
            else:
                return jsonify(status='error', message='Invalid device selection'), 400
        except Exception as e:
            # Fallback: restart from beginning if sox fails to construct
            if device == 'xr18':
                cmd = ['aplay', '-D', 'hw:3,0', filepath]
            elif device == 'x32':
                cmd = ['aplay', '-D', 'hw:XUSB,0', '-c', '32', '-r', '48000', '-f', 'S32_LE', filepath]
            else:
                return jsonify(status='error', message='Invalid device selection'), 400
        
        # Try to start the new process with retry mechanism
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Add some debugging
                print(f"Seek command (attempt {attempt + 1}): {' '.join(cmd)}")
                print(f"Seeking to position: {position} seconds")
                
                # Ensure device is free before each attempt
                force_release_device(device)
                
                proc = subprocess.Popen(cmd, shell=False)
                
                # Give it a moment to start and check if it's still running
                time.sleep(0.2)
                if proc.poll() is None:  # Process is still running
                    task['process'] = proc
                    task['mode'] = 'play'
                    return jsonify(status='seeking', position=position, file=filename)
                else:
                    # Process exited immediately, might be device busy
                    print(f"Process exited immediately, attempt {attempt + 1}")
                    if attempt < max_retries - 1:
                        time.sleep(0.5)  # Wait longer before retry
                        continue
                    else:
                        return jsonify(status='error', message='Audio device busy, please try again'), 500
                        
            except Exception as e:
                print(f"Seek attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
                else:
                    return jsonify(status='error', message=f'Seek failed after {max_retries} attempts: {str(e)}'), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)