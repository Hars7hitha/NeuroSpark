"""
server.py — NeuroFlow backend
Run: python server.py
Open: http://localhost:5000
"""

import json
import queue
import random
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
import os
load_dotenv()
import serial
import serial.tools.list_ports
from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, send_file, session)

from eeg_processor import EEGProcessor

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
MUSIC_DIR  = BASE_DIR / "music"
PROFILE_DB = DATA_DIR / "profiles.json"
DATA_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
DEFAULT_PORT   = "COM5"
DEFAULT_BAUD   = 115200

MUSIC_CATALOGUE = {
    "focused": {
        "instrumental": [f"f_mu{i}.mp3" for i in range(1, 6)],
        "lyrical":      [f"f_ly{i}.mp3" for i in range(1, 6)],
    },
    "relaxed": {
        "instrumental": [f"r_mu{i}.mp3" for i in range(1, 6)],
        "lyrical":      [f"r_ly{i}.mp3" for i in range(1, 6)],
    },
}

def available_tracks():
    out = {}
    for state, types in MUSIC_CATALOGUE.items():
        out[state] = {}
        for t, files in types.items():
            out[state][t] = [f for f in files if (MUSIC_DIR / f).exists()]
    return out

def pick_track(state, pref, exclude=None):
    avail = available_tracks()
    pool  = avail.get(state, {}).get(pref, [])
    if not pool:
        other = "lyrical" if pref == "instrumental" else "instrumental"
        pool  = avail.get(state, {}).get(other, [])
    if not pool:
        return None
    choices = [f for f in pool if f != exclude] or pool
    return random.choice(choices)

def load_db():
    if PROFILE_DB.exists():
        with open(PROFILE_DB) as f:
            return json.load(f)
    return {}

def save_db(db):
    with open(PROFILE_DB, "w") as f:
        json.dump(db, f, indent=2)

def get_user(username):
    return load_db().get(username)

def create_user(username, password):
    db = load_db()
    if username in db:
        return False, "Username already exists."
    db[username] = {
        "username":   username,
        "password":   password,
        "focus_pref": "instrumental",
        "relax_pref": "instrumental",
        "quiz_done":  False,
        "calibrated": False,
        "history":    [],
    }
    save_db(db)
    return True, "ok"

def authenticate(username, password):
    db = load_db()
    u  = db.get(username)
    if not u:                     return False, "User not found."
    if u["password"] != password: return False, "Wrong password."
    # Old profiles created before calibration feature — auto-mark as calibrated
    if "calibrated" not in u:
        u["calibrated"] = True
        db[username] = u
        save_db(db)
    return True, u

def update_user(username, fields):
    db = load_db()
    if username in db:
        db[username].update(fields)
        save_db(db)

def log_state(username, state, conf, ratios):
    db = load_db()
    if username not in db:
        return
    db[username]["history"].append({
        "ts":    time.strftime("%Y-%m-%d %H:%M:%S"),
        "state": state,
        "conf":  round(conf, 3),
        "alpha": round(ratios.get("alpha", 0), 4),
        "beta":  round(ratios.get("beta",  0), 4),
        "theta": round(ratios.get("theta", 0), 4),
    })
    if len(db[username]["history"]) > 5000:
        db[username]["history"] = db[username]["history"][-5000:]
    save_db(db)

processor   = EEGProcessor()
sse_clients = []
sse_lock    = threading.Lock()

serial_state = {
    "running": False,
    "port":    None,
    "error":   None,
    "samples": 0,
}

def broadcast(data: dict):
    msg = json.dumps(data)
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)

def serial_thread(port, baud=115200):
    serial_state["running"] = True
    serial_state["error"]   = None
    serial_state["port"]    = port

    try:
        ser = serial.Serial(port, baud, timeout=2)
        time.sleep(2)
        ser.read_all()
        print(f"[EEG] Connected on {port}")
    except Exception as e:
        serial_state["error"]   = str(e)
        serial_state["running"] = False
        print(f"[EEG] Failed: {e}")
        return

    last_log = 0

    while serial_state["running"]:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            raw = int(line)
            if not 0 <= raw <= 1023:
                continue
        except ValueError:
            continue
        except Exception as e:
            serial_state["error"] = str(e)
            break

        serial_state["samples"] += 1
        result = processor.push(raw)

        if result:
            now = time.time()
            if now - last_log > 10:
                username = serial_state.get("active_user")
                if username:
                    log_state(username, result["state"], result["conf"], result["ratios"])
                last_log = now
            broadcast({
                "type":    "eeg",
                "raw":     raw,
                "samples": serial_state["samples"],
                "result":  result,
            })

    ser.close()
    serial_state["running"] = False

def start_serial(port):
    if serial_state["running"]:
        return
    t = threading.Thread(target=serial_thread, args=(port,), daemon=True)
    t.start()

@app.route("/", methods=["GET"])
def index():
    if "username" in session:
        return redirect("/dashboard")
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    ok, result = authenticate(data["username"], data["password"])
    if not ok:
        return jsonify({"ok": False, "error": result})
    session["username"] = result["username"]
    serial_state["active_user"] = result["username"]
    return jsonify({"ok": True, "redirect": "/dashboard"})

@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json()
    u, p = data["username"].strip(), data["password"]
    if len(u) < 3:
        return jsonify({"ok": False, "error": "Username must be 3+ characters."})
    if len(p) < 4:
        return jsonify({"ok": False, "error": "Password must be 4+ characters."})
    ok, msg = create_user(u, p)
    if not ok:
        return jsonify({"ok": False, "error": msg})
    session["username"] = u
    serial_state["active_user"] = u
    return jsonify({"ok": True, "redirect": "/calibrate"})

@app.route("/logout")
def logout():
    session.clear()
    serial_state["active_user"] = None
    return redirect("/")

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/")
    profile = get_user(session["username"]) or {}
    if not profile.get("calibrated"):
        return redirect("/calibrate")
    return render_template("dashboard.html", profile=profile)

@app.route("/calibrate")
def calibrate():
    if "username" not in session:
        return redirect("/")
    return render_template("calibrate.html")

@app.route("/analytics")
def analytics():
    if "username" not in session:
        return redirect("/")
    profile = get_user(session["username"]) or {}
    history = profile.get("history", [])
    return render_template("analytics.html", profile=profile, history=json.dumps(history))

@app.route("/profile")
def profile_page():
    if "username" not in session:
        return redirect("/")
    profile = get_user(session["username"]) or {}
    tracks  = available_tracks()
    return render_template("profile.html", profile=profile, tracks=json.dumps(tracks))

@app.route("/api/profile", methods=["POST"])
def api_profile():
    if "username" not in session:
        return jsonify({"ok": False})
    data = request.get_json()
    update_user(session["username"], {
        "focus_pref": data.get("focus_pref", "instrumental"),
        "relax_pref": data.get("relax_pref", "instrumental"),
        "quiz_done":  True,
    })
    return jsonify({"ok": True})

@app.route("/api/connect", methods=["POST"])
def api_connect():
    if "username" not in session:
        return jsonify({"ok": False})
    data = request.get_json()
    port = data.get("port", DEFAULT_PORT)
    if serial_state["running"]:
        return jsonify({"ok": True, "msg": "Already running."})
    start_serial(port)
    return jsonify({"ok": True})

@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    serial_state["running"] = False
    return jsonify({"ok": True})

@app.route("/api/ports")
def api_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return jsonify({"ports": ports})

@app.route("/api/reset_calibration", methods=["POST"])
def reset_calibration():
    processor.reset()
    print("[RECALIBRATION] processor reset")
    return jsonify({"ok": True})

@app.route("/api/set_calibrated", methods=["POST"])
def set_calibrated():
    if "username" not in session:
        return jsonify({"ok": False})
    data  = request.get_json(silent=True) or {}
    value = data.get("value", True)
    update_user(session["username"], {"calibrated": value})
    return jsonify({"ok": True})

@app.route("/api/music/next", methods=["POST"])
def api_music_next():
    if "username" not in session:
        return jsonify({"ok": False})
    data    = request.get_json()
    state   = data.get("state", "relaxed")
    profile = get_user(session["username"]) or {}
    pref    = profile.get(f"{state}_pref", "instrumental")
    exclude = data.get("current")
    track   = pick_track(state, pref, exclude=exclude)
    return jsonify({"ok": True, "track": track})

@app.route("/api/status")
def api_status():
    return jsonify({
        "running": serial_state["running"],
        "port":    serial_state["port"],
        "error":   serial_state["error"],
        "samples": serial_state["samples"],
    })

@app.route("/api/history")
def api_history():
    if "username" not in session:
        return jsonify([])
    profile = get_user(session["username"]) or {}
    return jsonify(profile.get("history", []))

@app.route("/music/<filename>")
def serve_music(filename):
    path = MUSIC_DIR / filename
    if not path.exists():
        return "Not found", 404
    return send_file(path, mimetype="audio/mpeg")

@app.route("/stream")
def stream():
    if "username" not in session:
        return "Unauthorized", 401

    def event_stream(q):
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    q = queue.Queue(maxsize=50)
    with sse_lock:
        sse_clients.append(q)

    return Response(
        event_stream(q),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

start_serial(DEFAULT_PORT)

if __name__ == "__main__":
    print("NeuroFlow starting at http://localhost:5000")
    app.run(debug=False, threaded=True, port=5000)