# Raspberry Pi / Linux Multitrack Recorder with XR18 or X32

This guide sets up a Flask web app to control **multitrack recording and
playback** with the Behringer **XR18** or **X32** via USB.

------------------------------------------------------------------------

## 1️⃣ Prepare the System

Update and install dependencies:

``` bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv                  build-essential git                  alsa-utils sox                  libsndfile1-dev libasound2-dev -y
```

------------------------------------------------------------------------

## 2️⃣ Mount External SSD (exFAT)

Identify device:

``` bash
lsblk -f
```

Example output:

    sda1  exfat  Franek_Rec  UUID: 7225-476D

Create mount point:

``` bash
sudo mkdir -p /mnt/ssd
```

Edit **fstab**:

``` bash
sudo nano /etc/fstab
```

Add line (replace `UUID` with yours):

    UUID=7225-476D  /mnt/ssd  exfat  defaults,uid=1000,gid=1000  0  0

Reload and mount:

``` bash
sudo systemctl daemon-reexec
sudo mount -a
```

Create recordings folder:

``` bash
mkdir -p /mnt/ssd/recordings
```

------------------------------------------------------------------------

## 3️⃣ Verify Audio Device

List capture/playback devices:

``` bash
arecord -l
aplay -l
```

-   **XR18 → 18 channels**\
-   **X32 → 32 channels**

Device name usually `hw:3,0` (adjust as needed).

------------------------------------------------------------------------

## 4️⃣ Test Recording Manually

**XR18 (18 channels):**

``` bash
arecord -D hw:3,0 -f S32_LE -c 18 -r 48000   /mnt/ssd/recordings/test_xr18.wav
```

**X32 (32 channels):**

``` bash
arecord -D hw:3,0 -f S32_LE -c 32 -r 48000   /mnt/ssd/recordings/test_x32.wav
```

------------------------------------------------------------------------

## 5️⃣ Test Playback Manually

``` bash
aplay -D hw:3,0 /mnt/ssd/recordings/test.wav
```

⚠️ Notes:\
- On **XR18**, USB 17--18 map to **Main L/R** by default.\
- On **X32**, you must route USB returns to input channels in **Routing
→ Inputs**.

------------------------------------------------------------------------

## 6️⃣ Install the Flask Web App

Create project folder:

``` bash
mkdir ~/flask_recorder && cd ~/flask_recorder
python3 -m venv venv
source venv/bin/activate
pip install flask
```

**Project structure:**

    flask_recorder/
      app.py
      templates/
        index.html
      recordings/   # not used (we record to /mnt/ssd/recordings instead)

Copy the **Flask app** (`app.py`) and template (`index.html`) you built.

The UI includes buttons: **Record / Pause / Resume / Stop / Play /
Rewind / Next**.\
- Recording uses `arecord`\
- Playback uses `aplay`

Run the app:

``` bash
python app.py
```

Access via browser:

    http://<pi-ip>:5000

------------------------------------------------------------------------

## 7️⃣ Add Shutdown Button (Optional)

In `app.py`:

``` python
@app.route('/shutdown', methods=['POST'])
def shutdown():
    shutdown_func = request.environ.get('werkzeug.server.shutdown')
    if shutdown_func is None:
        return jsonify(status='error', message='Not running with Werkzeug'), 500
    shutdown_func()
    return jsonify(status='ok', message='Server shutting down...')
```

In `index.html`, add:

``` html
<button id="btn-shutdown">Shutdown Server</button>
<script>
document.getElementById('btn-shutdown').onclick = async () => {
  const res = await fetch('/shutdown', { method: 'POST' });
  const data = await res.json();
  alert(data.message);
};
</script>
```

------------------------------------------------------------------------

## 8️⃣ Routing Notes

-   **XR18:** USB 17/18 → Main L/R by default\
-   **X32:** Must configure routing in **Routing → Inputs** (assign Card
    1--32 to input channels)

------------------------------------------------------------------------

## ✅ End Result

-   Web UI controls **recording/playback of multitrack WAVs** to SSD\
-   Uses **arecord** for multichannel capture\
-   Uses **aplay** for playback\
-   Works with both **XR18 (18ch)** and **X32 (32ch)**\
-   Can be extended with **server shutdown/restart buttons**

------------------------------------------------------------------------
