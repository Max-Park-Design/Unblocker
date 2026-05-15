import csv
import json
import math
import random
import time
import re
import os
import qrcode
import threading
import serial
import sys
import tty
import termios
from PIL import Image as PILImage
from typing import Dict, List
from urllib.parse import urlparse

import requests
from mistralai.client import Mistral

import json

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump({"content_filter": CONTENT_FILTER, "emotional_state": EMOTIONAL_STATE}, f)

def load_state():
    global CONTENT_FILTER, EMOTIONAL_STATE
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            data = json.load(f)
            CONTENT_FILTER = data.get("content_filter", 2)
            EMOTIONAL_STATE = data.get("emotional_state", "stressed")
            print(f"Loaded state: filter={CONTENT_FILTER}, state={EMOTIONAL_STATE}")

# ---------------- HARDWARE ----------------
_serial = serial.Serial()
_serial.port = '/dev/ttyACM0'
_serial.baudrate = 115200
_serial.timeout = 1
_serial.dtr = False
_serial.open()
time.sleep(2)
_serial.reset_input_buffer()
_printer_port = '/dev/ttyUSB0'

_pot_values = [0.0] * 8
_total_rotation = 0.0
_last_angle = None
_rotation_lock = threading.Lock()
_pot_lock = threading.Lock()

_hr_buffer = []
_hr_reading = False
_hr_done = threading.Event()
_last_oled_values = [None, None, None]
_notebook_id = None
_led_fill_stop = False

_serial_lock = threading.Lock()

_usb_source_cache = {}  # slot_name -> source_id
_trigger_hr = False
_hr_cooldown = False
running = False
EMOTIONAL_STATE = "stressed"
CONTENT_FILTER = 3

# ---- USB PORT MAPPING ----
USB_PORT_MAP = {
    "usb-0:2.2.1": "USB1",
    "usb-0:2.2.2": "USB2",
    "usb-0:2.2.4": "USB3",
    "usb-0:2.4.1": "USB4",
    "usb-0:2.4.3": "USB5",
}

def get_usb_slot(device):
    path = device.get("ID_PATH", "")
    if not path and device.parent:
        path = device.parent.get("ID_PATH", "")
    for port, slot in USB_PORT_MAP.items():
        if port in path:
            return slot
    return None

def find_pdf_on_device(device_node, dest_path):
    import subprocess
    import tempfile
    try:
        mount_point = tempfile.mkdtemp(prefix="usb_")
        subprocess.run(["sudo", "mount", device_node, mount_point], check=True, capture_output=True)
        for fname in os.listdir(mount_point):
            if fname.lower().endswith(".pdf"):
                src = os.path.join(mount_point, fname)
                import shutil
                shutil.copy2(src, dest_path)
                print(f"  Copied {fname} to {dest_path}")
                subprocess.run(["sudo", "umount", mount_point], capture_output=True)
                return True
        subprocess.run(["sudo", "umount", mount_point], capture_output=True)
    except Exception as e:
        print(f"  Mount error: {e}")
    return False

def get_usb_slot(device):
    path = device.get("ID_PATH", "")
    if not path and device.parent:
        path = device.parent.get("ID_PATH", "")
    for port, slot in USB_PORT_MAP.items():
        if port in path:
            return slot
    return None


def usb_monitor_thread():
    import pyudev
    script_dir = os.path.dirname(os.path.abspath(__file__))
    usb_dir = os.path.join(script_dir, "usb_pdfs")
    os.makedirs(usb_dir, exist_ok=True)

    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="block")

    print("USB monitor started...")

    print("Scanning for existing USB drives...")
    for device in context.list_devices(subsystem='block', DEVTYPE='partition'):
        slot = get_usb_slot(device)
        if slot:
            print(f"  Found existing USB in {slot}")
            dest = os.path.join(usb_dir, f"{slot}.pdf")
            if find_pdf_on_device(device.device_node, dest):
                def embed_async(s=slot, p=dest):
                    print(f"  Extracting text from {s}...")
                    text = extract_pdf_text(p)
                    if not text:
                        return
                    import time as _t
                    for _ in range(60):
                        if _notebook_id:
                            break
                        _t.sleep(5)
                    if not _notebook_id:
                        print(f"  {s}: no notebook available after wait")
                        return
                    print(f"  Embedding {s} into Open Notebook...")
                    result = add_text_source(_notebook_id, f"USB {s}", text[:50000])
                    sid = result.get("id") or result.get("source_id")
                    if sid:
                        _usb_source_cache[s] = sid
                        print(f"  {s} embedded: {sid}, waiting for processing...")
                        wait_for_source_processing(sid)
                        print(f"  {s} ready!")
                    else:
                        print(f"  {s}: embedding failed")
                threading.Thread(target=embed_async, daemon=True).start()
            else:
                print(f"  No PDF found on {slot}")

    for device in iter(monitor.poll, None):
        action = device.action
        if device.device_type not in ("partition", "disk"):
            continue
        slot = get_usb_slot(device)
        if not slot:
            continue

        if action == "add" and device.device_type == "partition":
            print(f"  USB inserted in {slot}")
            import time as _time
            _time.sleep(1)
            dest = os.path.join(usb_dir, f"{slot}.pdf")
            if find_pdf_on_device(device.device_node, dest):
                def embed_async(s=slot, p=dest):
                    print(f"  Extracting text from {s}...")
                    text = extract_pdf_text(p)
                    if not text:
                        return
                    import time as _t
                    for _ in range(60):
                        if _notebook_id:
                            break
                        _t.sleep(5)
                    if not _notebook_id:
                        print(f"  {s}: no notebook available after wait")
                        return
                    print(f"  Embedding {s} into Open Notebook...")
                    result = add_text_source(_notebook_id, f"USB {s}", text[:50000])
                    sid = result.get("id") or result.get("source_id")
                    if sid:
                        _usb_source_cache[s] = sid
                        print(f"  {s} embedded: {sid}, waiting for processing...")
                        wait_for_source_processing(sid)
                        print(f"  {s} ready!")
                    else:
                        print(f"  {s}: embedding failed")
                threading.Thread(target=embed_async, daemon=True).start()
            else:
                print(f"  No PDF found on {slot}")

        elif action == "remove":
            if slot in _usb_source_cache:
                source_id = _usb_source_cache.pop(slot)
                delete_source(source_id)
                print(f"  {slot} removed from Open Notebook")
            dest = os.path.join(usb_dir, f"{slot}.pdf")
            if os.path.exists(dest):
                os.remove(dest)
                print(f"  {slot} PDF deleted")

def send_led(cmd):
    _serial.write(f"LED:{cmd}\n".encode())

def send_oled(idx, label, value):
    with _serial_lock:
        _serial.write(f"OLED:{idx}:{label}:{value:.2f}\n".encode())

def update_displays():
    global _last_oled_values
    if _hr_reading:
        return
    hw = get_hardware_inputs()
    vals = [hw["semantic_delta"], hw["overton"], hw["soft_power"]]
    labels = ["Semantic", "Overton", "Soft Power"]
    for i in range(3):
        rounded = round(vals[i], 2)
        if rounded != _last_oled_values[i]:
            send_oled(i, labels[i], rounded)
            _last_oled_values[i] = rounded
            time.sleep(0.05)

def get_hardware_inputs():
    with _pot_lock:
        vals = _pot_values[:]
    return {
        "semantic_delta": vals[0],
        "overton": vals[1],
        "soft_power": vals[2],
        "usb1": vals[3],
        "usb2": vals[4],
        "usb3": vals[5],
        "usb4": vals[6],
        "usb5": vals[7],
    }

def calc_hr_spo2_manual(ir_vals, red_vals, timestamps=None):
    ir_mean = sum(ir_vals) / len(ir_vals)
    ir_ac = max(ir_vals) - min(ir_vals)
    threshold = ir_mean - (ir_ac * 0.3)

    peaks = []
    for i in range(1, len(ir_vals) - 1):
        if ir_vals[i] > threshold and ir_vals[i] > ir_vals[i-1] and ir_vals[i] > ir_vals[i+1]:
            if not peaks or (timestamps and timestamps[i] - timestamps[peaks[-1]] > 600):
                peaks.append(i)

    print(f"  Peaks found: {len(peaks)}")
    if len(peaks) < 2:
        return None, None

    if timestamps and len(timestamps) > 1:
        total_time_s = (timestamps[peaks[-1]] - timestamps[peaks[0]]) / 1000.0
        if total_time_s == 0:
            return None, None
        hr = (len(peaks) - 1) / total_time_s * 60.0
    else:
        avg_interval = (peaks[-1] - peaks[0]) / (len(peaks) - 1)
        hr = 60.0 / (avg_interval / 100.0)

    # windowed SpO2
    window = 20
    r_vals = []
    for i in range(0, len(ir_vals) - window, window):
        ir_w = ir_vals[i:i+window]
        red_w = red_vals[i:i+window]
        ir_ac_w = max(ir_w) - min(ir_w)
        ir_dc_w = sum(ir_w) / len(ir_w)
        red_ac_w = max(red_w) - min(red_w)
        red_dc_w = sum(red_w) / len(red_w)
        if ir_dc_w > 0 and red_dc_w > 0 and ir_ac_w > 0:
            r_vals.append((red_ac_w / red_dc_w) / (ir_ac_w / ir_dc_w))

    spo2 = None
    if r_vals:
        r = sum(r_vals) / len(r_vals)
        spo2_raw = 104 - 17 * r
        if 90 <= spo2_raw <= 100:
            spo2 = spo2_raw
        else:
            print(f"  SpO2 out of range ({spo2_raw:.1f}%) — ignoring")

    return hr, spo2

def read_emotional_state(timeout=10):
    global _hr_buffer, _hr_reading
    _hr_reading = True

    print("Reading emotional state...")
    _hr_buffer = []
    _hr_done.clear()
    with _serial_lock:
        _serial.write(b"READ_HR\n")

    send_led("RED:1")
    last_led = 1
    settling_count = 0

    while not _hr_done.is_set():
        settling_count = len([l for l in _hr_buffer if l == "SETTLING"])
        sample_count = len([l for l in _hr_buffer if l.startswith("RAW:")])
        settle_leds = min(7, settling_count + 1)
        sample_leds = min(8, int((sample_count / 195) * 8))
        led_level = min(15, settle_leds + sample_leds)
        if led_level != last_led:
            send_led(f"RED:{led_level}")
            last_led = led_level
        time.sleep(0.1)

    # drain effect
    send_led("OFF")
    time.sleep(0.1)
    for i in range(15, -1, -1):
        send_led(f"RED:{i}" if i > 0 else "OFF")
        time.sleep(0.05)
    send_led("OFF")

    _hr_reading = False

    timestamps = []
    red_vals = []
    ir_vals = []
    for line in _hr_buffer:
        parts = line.split(":")
        if len(parts) == 4:
            try:
                red_vals.append(int(parts[1]))
                ir_vals.append(int(parts[2]))
                timestamps.append(int(parts[3]))
            except Exception:
                pass

    print(f"Got {len(ir_vals)} samples")
    if ir_vals:
        print(f"  IR range: {min(ir_vals)} - {max(ir_vals)}, avg: {sum(ir_vals)//len(ir_vals)}")
        print(f"  RED range: {min(red_vals)} - {max(red_vals)}, avg: {sum(red_vals)//len(red_vals)}")
        if timestamps:
            print(f"  Duration: {timestamps[-1]}ms")

    if not ir_vals or len(ir_vals) < 25:
        print("Not enough data")
        send_led("OFF")
        return None

    filtered = [(r, i, t) for r, i, t in zip(red_vals, ir_vals, timestamps) if i > 50000]
    print(f"  Finger-present samples: {len(filtered)}")
    if len(filtered) < 50:
        print(f"No finger detected ({len(filtered)} valid samples)")
        send_led("OFF")
        return None

    red_calc = [r for r, i, t in filtered][:500]
    ir_calc = [i for r, i, t in filtered][:500]
    ts_calc = [t for r, i, t in filtered][:500]

    while len(ir_calc) < 500:
        ir_calc.append(ir_calc[-1])
        red_calc.append(red_calc[-1])
        ts_calc.append(ts_calc[-1])

    hr, spo2 = calc_hr_spo2_manual(ir_calc, red_calc, ts_calc)

    if hr is None:
        print("Could not calculate HR")
        send_led("OFF")
        return None

    hr_valid = 40 < hr < 180
    if not hr_valid:
        print(f"Invalid HR: {hr:.0f}")
        send_led("OFF")
        return None

    if spo2:
        print(f"HR: {hr:.0f} bpm | SpO2: {spo2:.1f}%")
    else:
        print(f"HR: {hr:.0f} bpm | SpO2: unavailable")

    high_hr = hr > 80
    high_spo2 = spo2 is not None and spo2 > 97

    if high_spo2 and high_hr:
        state = "excited"
    elif high_spo2 and not high_hr:
        state = "stressed"
    elif not high_spo2 and high_hr:
        state = "focused"
    else:
        state = "depressed"

    print(f"Emotional state: {state}")
    send_led("OFF")
    return state

def serial_reader_thread():
    global _pot_values, _total_rotation, _last_angle, running, EMOTIONAL_STATE, CONTENT_FILTER

    while True:
        try:
            line = _serial.readline().decode('utf-8').strip()
            if not line:
                continue

            if line.startswith("POTS:"):
                values = line.split(":")[1:]
                if len(values) == 8:
                    with _pot_lock:
                        _pot_values = [1.0 - float(v) for v in values]

            elif line.startswith("ANGLE:"):
                angle = float(line.split(":")[1])
                with _rotation_lock:
                    if _last_angle is not None:
                        diff = angle - _last_angle
                        if diff > 180:
                            diff -= 360
                        elif diff < -180:
                            diff += 360
                        _total_rotation += diff
                    _last_angle = angle

            elif line.startswith("REED:"):
                reed = int(line.split(":")[1])
                CONTENT_FILTER = reed
                print(f"Content filter set to: {CONTENT_FILTER}")
                save_state()

            elif line == "LIMIT:1":
                if not running and not _hr_cooldown:
                    _trigger_hr = True

            elif line == "SHUTDOWN":
                print("Shutdown button pressed!")
                os.system("sudo shutdown now")

            elif line.startswith("RAW:"):
                _hr_buffer.append(line)
                print(f"  Buffer: {len(_hr_buffer)} samples")

            elif line == "HR_DONE":
                print("  HR_DONE received")
                _hr_done.set()

            elif line == "SETTLING":
                _hr_buffer.append(line)

        except Exception:
            pass

def led_progress(step, total_steps=9):
    target = int((step / total_steps) * 15)
    current = led_progress._current if hasattr(led_progress, '_current') else 0
    for i in range(current + 1, target + 1):
        send_led(f"WHITE:{i}")
        time.sleep(1.0)
    led_progress._current = target

def led_reset():
    led_progress._current = 0
    send_led("OFF")

def led_fill_slowly(from_led, to_led, duration):
    global _led_fill_stop
    _led_fill_stop = False
    if to_led <= from_led:
        return
    delay = duration / (to_led - from_led)
    for i in range(from_led + 1, to_led + 1):
        if _led_fill_stop:
            return
        send_led(f"WHITE:{i}")
        time.sleep(delay)
    led_progress._current = to_led

def led_complete():
    for _ in range(3):
        send_led("WHITE:15")
        time.sleep(0.3)
        send_led("OFF")
        time.sleep(0.3)
    send_led("WHITE:15")
    time.sleep(1.0)
    send_led("OFF")

def send_print(text, image_path=None, qr_url=None):
    from escpos.printer import Serial as EscposSerial
    import textwrap

    printer = EscposSerial(
        devfile=_printer_port,
        baudrate=9600,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=2,
        dsrdtr=False,
    )

    printer._raw(b"\x1b\x40")
    wrapped = textwrap.fill(text, width=32)
    printer.text(wrapped + "\n\n")

    if image_path and os.path.exists(image_path):
        try:
            printer.image(image_path, impl="bitImageRaster")
            printer._raw(b"\x1b\x40")
            printer.text("\n")
        except Exception as e:
            print(f"  Image print failed: {e}")

    if qr_url:
        try:
            qr = qrcode.QRCode(version=1, box_size=4, border=2)
            qr.add_data(qr_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_img.convert("RGB")
            qr_path = "/tmp/print_qr.png"
            qr_img.save(qr_path)
            printer._raw(b"\x1b\x61\x01")
            printer.image(qr_path, impl="bitImageRaster")
            printer._raw(b"\x1b\x61\x00")
            printer.text("\n")
        except Exception as e:
            print(f"  QR print failed: {e}")

    printer.text("\n\n\n\n\n")
    printer._raw(b"\x1d\x56\x00")

# ---------------- CONFIG ----------------
MISTRAL_API_KEY = "QEjxW7dOoDcSu7dvtgXldxNMrILiEstq"
WIKIPEDIA_API = "https://en.wikipedia.org/api/rest_v1/page/summary"
OPEN_NOTEBOOK_API = "http://localhost:5055"
MAX_FINAL_SOURCES = 5
client = Mistral(api_key=MISTRAL_API_KEY, timeout_ms=60000)

SOFT_POWER_TEMPERATURE = 0.08
INDEX_CSV_PATH = "soft_power_index.csv"
SEMANTIC_DELTA = 0.4

# ---------------- CONTENT FILTER ----------------

CONTENT_FILTER_GUIDANCE = {
    1: (
        "U/PG (Universal/Parental Guidance) — suitable for all ages. "
        "Sources and angles must be appropriate for children and families. "
        "Mild conflict, historical events, and social issues are acceptable if handled "
        "with sensitivity and care. Avoid anything frightening, disturbing, or distressing. "
        "Focus on discovery, culture, human achievement, and the world presented with curiosity."
    ),
    2: (
        "15 — suitable for ages 15 and over, following BBFC guidelines. "
        "Sources and angles may include strong language, drug use, violence without excessive "
        "detail, and complex or disturbing social and political content. Discrimination, "
        "extremism, trauma, grief, and human suffering may be examined honestly and critically. "
        "Sexual content should remain non-explicit."
    ),
    3: (
        "18 — suitable for adults only, following BBFC guidelines. "
        "No content restrictions beyond credibility and serious intent. "
        "Prioritise uncomfortable, confronting, or taboo angles of the topic. "
        "Prioritise honesty and depth over palatability."
    ),
}

CONTENT_FILTER_LANGUAGE = {
    1: "Write for a child. Use simple, clear, warm language. No complex vocabulary, no darkness, no ambiguity.",
    2: "Write for an adult who can handle complexity and provocation. The tone should feel like an honest, intelligent friend who doesn't soften things unnecessarily.",
    3: "Write provocatively without any linguistic restraint. Do not sanitise. Do not hedge. The tone should feel like someone who has no filter and no fear.",
}

CONTENT_FILTER_LABELS = {1: "U/PG", 2: "15", 3: "18"}

def get_content_filter_language(content_filter: int) -> str:
    return CONTENT_FILTER_LANGUAGE.get(content_filter, CONTENT_FILTER_LANGUAGE[1])

def get_content_filter_guidance(content_filter: int) -> str:
    return CONTENT_FILTER_GUIDANCE.get(content_filter, CONTENT_FILTER_GUIDANCE[1])

def get_content_filter_label(content_filter: int) -> str:
    return CONTENT_FILTER_LABELS.get(content_filter, "U/PG")

USB_SLOTS = {
    "USB1": {"path": "usb_pdfs/USB1.pdf", "dial": 0.0},
    "USB2": {"path": "usb_pdfs/USB2.pdf", "dial": 0.0},
    "USB3": {"path": "usb_pdfs/USB3.pdf", "dial": 0.0},
    "USB4": {"path": "usb_pdfs/USB4.pdf", "dial": 0.0},
    "USB5": {"path": "usb_pdfs/USB5.pdf", "dial": 0.0},
}

# ---------------- EMOTIONAL STATE DEFINITIONS ----------------

EMOTIONAL_STATES = {
    "depressed": {
        "description": "low oxygen, low pulse — subdued, withdrawn, low energy",
        "prompt_modifier": "The person reading this is feeling low and withdrawn. Prioritise warmth, wonder, and the unexpected beauty of ordinary things.",
        "topic_modifier": "Favour topics that are grounding, human, and quietly uplifting. Avoid topics that are confrontational, abstract, or heavy.",
    },
    "stressed": {
        "description": "high oxygen, low pulse — tense, overloaded, anxious",
        "prompt_modifier": "The person reading this is feeling stressed and overwhelmed. Prioritise clarity, perspective-shifting, and calm provocation.",
        "topic_modifier": "Favour topics that offer perspective on human systems, history, or problem-solving. Avoid topics that add to a sense of chaos or overwhelm.",
    },
    "excited": {
        "description": "high oxygen, high pulse — energised, open, ready",
        "prompt_modifier": "The person reading this is feeling excited and energised. Prioritise the unexpected, the radical, and the generative.",
        "topic_modifier": "Favour topics that are at the edges of human knowledge or practice. Embrace the strange and the ambitious.",
    },
    "focused": {
        "description": "low oxygen, high pulse — concentrated, purposeful, ready to work",
        "prompt_modifier": "The person reading this is in a focused, purposeful state. Prioritise precision, specificity, and actionability.",
        "topic_modifier": "Favour topics that have clear structure, defined problems, or practical dimensions. Avoid topics that are too diffuse or open-ended.",
    },
}

def get_emotional_state_data(state: str) -> dict:
    return EMOTIONAL_STATES.get(state, EMOTIONAL_STATES["focused"])

# ---------------- TOPIC LIST ----------------

TOPICS = [
    "Folklore and mythology", "Proverbs", "Music", "Dance",
    "Visual arts and sculpture", "Cuisine and food traditions",
    "Clothing and traditional dress", "Language and dialects", "Humour and comedy",
    "Religion and spirituality", "Philosophy and ethics", "Gender roles and traditions",
    "Coming-of-age rituals", "Death and burial customs", "Politics and government",
    "Legal systems and justice", "Military history", "National identity and independence",
    "Colonial history", "Borders and territorial disputes", "Literature and oral storytelling",
    "Architecture", "Film and theatre", "Sport and games", "Ancient philosophy",
    "Ethics and moral traditions", "Logic and reasoning traditions", "Political philosophy",
    "Epistemology and ways of knowing", "Traditional medicine and healing",
    "Astronomy and cosmology", "Mathematics and numeracy", "Natural history and ecology",
    "Modern scientific contributions", "Agricultural techniques",
    "Architecture and civil engineering", "Weapons and warfare technology",
    "Maritime and navigation history", "Industrial and technological development",
    "Natural resources", "Environmental challenges", "Urban and rural life",
    "Agriculture and land use", "Social inequality and class", "Immigration and diaspora",
    "Festivals and holidays", "Superstitions and taboos", "Health and disease history",
    "Genocide and mass atrocity", "Slavery and forced labour", "Torture and punishment",
    "Child mortality and infanticide", "Famine and starvation", "Cult movements",
    "Human experimentation", "Organised crime and cartels", "Political imprisonment",
    "Censorship and propaganda", "Surveillance and control", "Extremism and radicalisation",
    "Sex work and exploitation", "Addiction and substance abuse", "Suicide and self-destruction",
    "Animal cruelty and extinction", "Environmental catastrophe", "Nuclear warfare",
    "Biological and chemical weapons", "Corruption and kleptocracy", "Ethnic cleansing",
    "Religious persecution", "Forced marriage and honour violence", "Human trafficking",
    "Police brutality and state violence", "Psychological manipulation and gaslighting",
]

def pick_topic() -> str:
    return random.choice(TOPICS)

def generate_delta_topic(original_topic: str, delta: float) -> str:
    pct = int(delta * 100)
    response = client.chat.complete(
        model="mistral-small-latest",
        messages=[{
            "role": "user",
            "content": (
                f"Given the topic '{original_topic}', generate a visual subject that is {pct}% conceptually distant from it. "
                f"At 0% it directly illustrates the topic. At 100% it feels completely unrelated. "
                f"At {pct}% the connection should feel {'direct and obvious' if delta < 0.2 else 'tangential but recognisable' if delta < 0.5 else 'loose and poetic' if delta < 0.8 else 'completely absent'}. "
                f"It must exist as a real photograph on Wikimedia Commons. "
                f"Pick something real, well-known, and easy to find photographs of. "
                f"Return only the subject name. No explanation. No punctuation at the end. 2-5 words maximum."
            )
        }],
        temperature=0.3 + (delta * 0.7),
        max_tokens=15,
    )
    return response.choices[0].message.content.strip()

    response = client.chat.complete(
        model="mistral-small-latest",
        messages=[{
            "role": "user",
            "content": (
                f"{instruction} "
                f"It must exist as a real photograph on Wikimedia Commons. "
                f"Favour well-known natural phenomena, famous locations, iconic artworks, or documented historical events. "
                f"Return only the subject name. No explanation. No punctuation at the end. "
                f"2-5 words maximum. Be specific but not obscure."
            )
        }],
        temperature=0.3 + (delta * 0.7),
        max_tokens=15,
    )
    return response.choices[0].message.content.strip()

def calculate_source_weights(usb_pdfs: list[dict]) -> dict:
    web_weight = 1.0
    total = web_weight + sum(pdf["dial"] for pdf in usb_pdfs)
    weights = {"web": round(web_weight / total, 3)}
    for pdf in usb_pdfs:
        weights[pdf["slot"]] = round(pdf["dial"] / total, 3)
    return weights

def find_specific_subject(topic: str) -> str:
    response = client.chat.complete(
        model="mistral-medium-2505",
        messages=[
            {"role": "system", "content": "You identify a single specific, visually compelling subject for a Wikimedia Commons image search. Return ONLY the subject name. 2-5 words. No explanation."},
            {"role": "user", "content": f"Topic: {topic}\n\nGive me the single most specific, visually striking subject."},
        ],
        temperature=0.9,
        max_tokens=20,
    )
    return response.choices[0].message.content.strip().strip('"').strip("'")

# ---------------- OVERTON WINDOW ----------------

OVERTON_SOURCE_TIERS = [
    (0.0, {"government", "major_newspaper", "public_broadcaster", "reference", "international_org"}),
    (0.2, {"university", "research_institute", "industry_body", "think_tank", "library", "archive", "museum", "cultural_institution"}),
    (0.4, {"ngo", "magazine", "news_site"}),
    (0.6, {"investigative_outlet", "civil_society"}),
    (0.8, {"heterodox_academic", "activist_org", "community_media", "oral_history", "zine"}),
]

OVERTON_LABELS = [
    (0.0,  0.15, "policy"),
    (0.15, 0.35, "popular"),
    (0.35, 0.55, "sensible"),
    (0.55, 0.70, "acceptable"),
    (0.70, 0.85, "radical"),
    (0.85, 1.01, "unthinkable"),
]

OVERTON_PROMPT_GUIDANCE = {
    "policy": "Prioritise official and institutional sources: government bodies, major newspapers, public broadcasters, and established international organisations.",
    "popular": "Draw from widely trusted sources including major newspapers, public broadcasters, well-known think tanks, universities, and established reference sources.",
    "sensible": "Cast a wide net across credible mainstream and institutional sources. Universities, research institutes, NGOs, established media, museums, archives, and libraries are all appropriate.",
    "acceptable": "Include sources that represent a broad spectrum of credible viewpoints, including those that challenge mainstream consensus from legitimate positions.",
    "radical": "Actively seek out heterodox, dissenting, and minority credible viewpoints. Prioritise investigative outlets, activist organisations, community media, civil society groups.",
    "unthinkable": "Seek sources representing viewpoints currently at the margins of acceptable discourse but grounded in evidence, lived experience, or serious argument.",
}

def get_overton_label(dial: float) -> str:
    for low, high, label in OVERTON_LABELS:
        if low <= dial < high:
            return label
    return "sensible"

def get_overton_prompt_guidance(dial: float) -> str:
    return OVERTON_PROMPT_GUIDANCE[get_overton_label(dial)]

def get_allowed_source_types(dial: float) -> set:
    allowed = set()
    for min_dial, types in OVERTON_SOURCE_TIERS:
        if dial >= min_dial:
            allowed |= types
    return allowed

# ---------------- BLACKLISTED DOMAINS ----------------

BLACKLISTED_DOMAINS = {
    "facebook.com", "instagram.com", "tiktok.com", "reddit.com",
    "quora.com", "medium.com", "pinterest.com",
}

# ---------------- COUNTRY SELECTION ----------------

def load_soft_power_csv(path: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                score = float(row["score"].strip())
            except (ValueError, TypeError):
                continue
            out[row["country"].strip()] = score
    if not out:
        raise ValueError("No valid countries loaded.")
    return out

def _minmax(values: List[float]) -> List[float]:
    mn, mx = min(values), max(values)
    if mx == mn:
        return [0.5] * len(values)
    return [(v - mn) / (mx - mn) for v in values]

def choose_country(soft_power: Dict[str, float], soft_power_dial: float, temperature: float) -> str:
    countries = list(soft_power.keys())
    scores = [soft_power[c] for c in countries]
    t = _minmax(scores)
    preference = [(1.0 - soft_power_dial) * ti + soft_power_dial * (1.0 - ti) for ti in t]
    mean_pref = sum(preference) / len(preference)
    logits = [(p - mean_pref) / temperature for p in preference]
    m = max(logits)
    exps = [math.exp(l - m) for l in logits]
    total = sum(exps)
    probs = [e / total for e in exps]
    return random.choices(countries, weights=probs, k=1)[0]

# ---------------- UTILITIES ----------------

def load_usb_pdfs(usb_slots: dict) -> list[dict]:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    active = []
    for slot_name, slot_config in usb_slots.items():
        dial = slot_config.get("dial", 0.0)
        if dial <= 0.0:
            continue
        path = slot_config.get("path", "")
        full_path = os.path.join(script_dir, path)
        if not os.path.exists(full_path):
            continue
        text = _usb_text_cache.get(slot_name, "")
        if not text:
            print(f"  {slot_name}: not yet cached, skipping")
            continue
        active.append({"slot": slot_name, "path": full_path, "dial": dial, "text": text})
        print(f"  ✓ {slot_name} ACTIVE — {len(text)} chars, dial={dial:.2f}")
    if not active:
        print("  No active USB PDFs")
    else:
        print(f"  {len(active)} USB PDF(s) active")
    return active

def extract_pdf_text(pdf_path: str) -> str | None:
    try:
        import PyPDF2
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
        return text.strip() or None
    except Exception as e:
        print(f"  PDF extraction failed for {pdf_path}: {e}")
        return None

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def download_image(url: str, filename: str = None) -> str | None:
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        images_dir = os.path.join(script_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        elif "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"
        else:
            ext = os.path.splitext(url.split("?")[0])[-1] or ".jpg"
        if not filename:
            filename = f"delta_image_{int(time.time())}{ext}"
        output_path = os.path.join(images_dir, filename)
        with open(output_path, "wb") as f:
            f.write(response.content)
        print(f"  Image saved to: {output_path}")
        return output_path
    except Exception as e:
        print(f"  Image download failed: {e}")
        return None

def extract_message_content(response) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    outputs = getattr(response, "outputs", None)
    if outputs:
        for entry in outputs:
            entry_type = getattr(entry, "type", None)
            content = getattr(entry, "content", None)
            if entry_type == "message.output" and isinstance(content, str):
                return content.strip()
    raise ValueError(f"Could not extract assistant message content from response:\n{response}")

def clean_json_text(text: str) -> str:
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("```", 1)[1].rsplit("```", 1)[0].strip()
    cleaned = _repair_json(cleaned)
    return cleaned

def _repair_json(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    text = re.sub(r",\s*$", "", text)
    text += "}" * max(0, open_braces)
    text += "]" * max(0, open_brackets)
    return text

def url_looks_fetchable(url: str) -> tuple[bool, str]:
    blocked_markers = ["captcha", "verify you are not a bot", "security verification", "access denied", "forbidden", "cloudflare", "please enable javascript"]
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}, allow_redirects=True)
    except Exception as e:
        return False, f"request failed: {e}"
    if response.status_code >= 400:
        return False, f"http {response.status_code}"
    text = response.text[:5000].lower()
    for marker in blocked_markers:
        if marker in text:
            return False, f"blocked page detected: {marker}"
    return True, "ok"

def process_image_for_thermal(input_path: str) -> str | None:
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        thermal_dir = os.path.join(script_dir, "thermal")
        os.makedirs(thermal_dir, exist_ok=True)
        img = PILImage.open(input_path)
        img = img.convert("L")
        thermal_width = 384
        aspect = img.height / img.width
        thermal_height = int(thermal_width * aspect)
        img = img.resize((thermal_width, thermal_height), PILImage.LANCZOS)
        from PIL import ImageEnhance
        img = ImageEnhance.Contrast(img).enhance(1.8)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        img = img.convert("1", dither=PILImage.Dither.FLOYDSTEINBERG)
        filename = os.path.splitext(os.path.basename(input_path))[0] + "_thermal.png"
        output_path = os.path.join(thermal_dir, filename)
        img.save(output_path)
        print(f"  Thermal image saved: {output_path}")
        return output_path
    except Exception as e:
        print(f"  Thermal processing failed: {e}")
        return None

# ---------------- SOURCE FILTERING ----------------

def is_good_source(source: dict, allowed_source_types: set) -> bool:
    url = source.get("url", "").strip()
    source_type = source.get("source_type", "").strip().lower()
    try:
        credibility = float(source.get("credibility_score", 0))
    except Exception:
        credibility = 0.0
    try:
        country_relevance = float(source.get("country_relevance_score", 0))
    except Exception:
        country_relevance = 0.0
    try:
        topic_relevance = float(source.get("topic_relevance_score", 0))
    except Exception:
        topic_relevance = country_relevance
    if not url.startswith("http"):
        return False
    domain = domain_of(url)
    if not domain or domain in BLACKLISTED_DOMAINS:
        return False
    if source_type not in allowed_source_types:
        return False
    if credibility < 0.60:
        return False
    if country_relevance < 0.40:
        return False
    if topic_relevance < 0.55:
        return False
    return True

def source_score(source: dict) -> float:
    try:
        credibility = float(source.get("credibility_score", 0) or 0)
    except Exception:
        credibility = 0.0
    try:
        country_relevance = float(source.get("country_relevance_score", 0) or 0)
    except Exception:
        country_relevance = 0.0
    try:
        topic_relevance = float(source.get("topic_relevance_score", 0) or 0)
    except Exception:
        topic_relevance = country_relevance
    return (0.45 * credibility) + (0.20 * country_relevance) + (0.35 * topic_relevance)

def dedupe_and_rank_sources(sources: list[dict]) -> list[dict]:
    ranked = sorted(sources, key=source_score, reverse=True)
    seen_urls = set()
    domain_counts = {}
    final_sources = []
    for source in ranked:
        url = source.get("url", "").strip()
        if not url or url in seen_urls:
            continue
        domain = domain_of(url)
        if domain_counts.get(domain, 0) >= 2:
            continue
        seen_urls.add(url)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        final_sources.append(source)
        if len(final_sources) >= MAX_FINAL_SOURCES:
            break
    return final_sources

# ---------------- MISTRAL: SOURCE FINDING ----------------

def find_sources_with_mistral(country: str, topic: str, overton_dial: float, content_filter: int, emotional_state: str) -> list[dict]:
    print("  Starting Mistral API call...")
    overton_guidance = get_overton_prompt_guidance(overton_dial)
    allowed_source_types = get_allowed_source_types(overton_dial)
    allowed_types_str = "|".join(sorted(allowed_source_types))
    filter_guidance = get_content_filter_guidance(content_filter)
    state_data = get_emotional_state_data(emotional_state)
    topic_modifier = state_data["topic_modifier"]

    prompt = f"""
    Find up to 5 credible and relevant online sources for this topic in this country.

    Country: {country}
    Topic: {topic}

    Source selection guidance: {overton_guidance}
    Content guidance: {filter_guidance}
    Emotional context: {topic_modifier}

    Rules:
    - Return between 0 and 15 candidate sources.
    - Only include sources matching these types: {allowed_types_str}
    - Return ONLY JSON.

    JSON schema:
    {{
    "sources": [
        {{
        "title": "string",
        "url": "string",
        "publisher": "string",
        "country": "string",
        "source_type": "{allowed_types_str}",
        "credibility_score": 0.0,
        "country_relevance_score": 0.0,
        "topic_relevance_score": 0.0,
        "why_credible": "string",
        "why_relevant": "string"
        }}
    ]
    }}
    """.strip()

    for attempt in range(3):
        try:
            response = client.beta.conversations.start(
                inputs=prompt,
                model="mistral-medium-2505",
                instructions=(
                    "You find relevant, credible online sources for the requested topic in the requested country. "
                    f"Source selection guidance: {overton_guidance} "
                    f"Content guidance: {filter_guidance} "
                    f"Emotional context for topic angle: {topic_modifier} "
                    "Return ONLY valid JSON. "
                    "If you cannot find any sources, return {\"sources\": []} and nothing else. "
                    "NEVER return explanatory text. ALWAYS return valid JSON."
                ),
                tools=[{"type": "web_search"}],
                completion_args={"temperature": 0.2, "top_p": 0.9},
                timeout_ms=90000,
            )
            break
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  Retry {attempt + 1} after timeout: {e}")
            time.sleep(2)

    print("  Mistral API call complete!")
    text = extract_message_content(response)
    print("\n--- EXTRACTED MISTRAL MESSAGE START ---")
    print(text)
    print("--- EXTRACTED MISTRAL MESSAGE END ---\n")

    cleaned = clean_json_text(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print("Failed to parse Mistral response as JSON.")
        print(str(e))
        return []

    candidates = data.get("sources", [])
    filtered = [source for source in candidates if is_good_source(source, allowed_source_types)]
    return dedupe_and_rank_sources(filtered)

# ---------------- MISTRAL: PROMPT GENERATION ----------------

def get_notebook_prompt(emotional_state: str, content_filter: int) -> str:
    state_data = get_emotional_state_data(emotional_state)
    modifier = state_data["prompt_modifier"]
    language = get_content_filter_language(content_filter)
    return (
        "From the source texts, extract the single most interesting, shocking, or counterintuitive fact or insight. "
        "You may draw on multiple sources if they combine to form something more compelling than any single fact alone. "
        "The result must be cohesive, faithful to the sources, and above all interesting. "
        "Favour the unexpected, the disturbing, the paradoxical, or the revelatory over the mundane. "
        "Write it as one punchy sentence in the style of an intelligent newspaper headline. "
        "Do NOT write poetry, metaphors, or travel writing. "
        "Do NOT use 'while', 'whereas', or 'meanwhile' to join unrelated facts. "
        "No preamble. No caveats. No attribution. No citations. One sentence only. "
        f"{modifier} {language}"
    )

# ---------------- OPEN NOTEBOOK ----------------

def get_latest_notebook() -> dict:
    response = requests.get(f"{OPEN_NOTEBOOK_API}/api/notebooks?order_by=updated+desc", timeout=30)
    response.raise_for_status()
    notebooks = response.json()
    if not notebooks:
        raise RuntimeError("No notebooks found.")
    return notebooks[0]

def get_sources_in_notebook(notebook_id: str) -> list:
    response = requests.get(f"{OPEN_NOTEBOOK_API}/api/sources", params={"notebook_id": notebook_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []

def delete_source(source_id: str) -> tuple[bool, str]:
    response = requests.delete(f"{OPEN_NOTEBOOK_API}/api/sources/{source_id}", timeout=60)
    if 200 <= response.status_code < 300:
        return True, response.text
    return False, response.text

def clear_notebook_sources(notebook_id: str) -> dict:
    initial_sources = get_sources_in_notebook(notebook_id)
    print(f"Found {len(initial_sources)} existing source(s). Deleting...")
    usb_source_ids = set(_usb_source_cache.values())
    to_delete = [s.get("id") for s in initial_sources if s.get("id") and s.get("id") not in usb_source_ids]

    deleted = []
    failed = []

    def do_delete(source_id):
        ok, body = delete_source(source_id)
        if ok:
            deleted.append(source_id)
        else:
            failed.append({"source_id": source_id, "response": body})

    threads = [threading.Thread(target=do_delete, args=(sid,)) for sid in to_delete]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    time.sleep(1)
    remaining = get_sources_in_notebook(notebook_id)
    remaining_ids = [s.get("id") for s in remaining if s.get("id")]
    print(f"Remaining sources after clear: {len(remaining_ids)}")
    return {"initial_count": len(initial_sources), "deleted_ids": deleted, "failed_deletions": failed, "remaining_count": len(remaining_ids), "remaining_ids": remaining_ids}

def add_link_source(notebook_id: str, url: str, retries: int = 5, delay: float = 2.0) -> dict:
    data = {"type": "link", "notebook_id": notebook_id, "url": url, "embed": "true", "async_processing": "true"}
    for attempt in range(retries):
        try:
            response = requests.post(f"{OPEN_NOTEBOOK_API}/api/sources", data=data, timeout=120)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError:
            if response.status_code == 500 and attempt < retries - 1:
                time.sleep(delay)
                delay *= 1.5
            else:
                raise
    raise Exception(f"Failed to add source after {retries} retries: {url}")

def add_text_source(notebook_id: str, title: str, text: str) -> dict:
    data = {"type": "text", "notebook_id": notebook_id, "title": title, "content": text, "embed": "true", "async_processing": "true"}
    for attempt in range(3):
        try:
            response = requests.post(f"{OPEN_NOTEBOOK_API}/api/sources", data=data, timeout=120)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  add_text_source retry {attempt+1}: {e}")
            time.sleep(2)

def wait_for_source_processing(source_id: str, timeout: int = 300) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(f"{OPEN_NOTEBOOK_API}/api/sources/{source_id}", timeout=30)
            response.raise_for_status()
            data = response.json()
            embedded = data.get("embedded", False)
            embedded_chunks = data.get("embedded_chunks", 0)
            status = data.get("status", "").lower()
            print(f"  Source {source_id} — embedded: {embedded}, chunks: {embedded_chunks}, status: {status}")
            if embedded and embedded_chunks > 0:
                return True
            if status in ("failed", "error"):
                return False
        except Exception as e:
            print(f"  Status check failed: {e}")
        time.sleep(5)
    return False

def fetch_source_content(source_id: str) -> str | None:
    try:
        response = requests.get(f"{OPEN_NOTEBOOK_API}/api/sources/{source_id}", timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("full_text") or None
    except Exception as e:
        print(f"  Could not fetch source content: {e}")
        return None

def get_wikipedia_summary(url: str) -> str | None:
    try:
        title = url.rstrip("/").split("/wiki/")[-1]
        response = requests.get(f"{WIKIPEDIA_API}/{title}", timeout=20, headers={"User-Agent": "open-notebook-pipeline/1.0"})
        if response.status_code != 200:
            return None
        data = response.json()
        return data.get("extract", "").strip() or None
    except Exception as e:
        print(f"  Wikipedia API error for {url}: {e}")
        return None

def _run_query(prompt: str, source_text: str) -> str:
    response = client.chat.complete(
        model="mistral-medium-2505",
        messages=[
            {"role": "system", "content": (
                "You are a research assistant. Answer using ONLY the source texts provided. "
                "Extract ONE specific fact, statistic, name, date, or concrete piece of information. "
                "Do NOT write poetry, metaphors, travel writing, or general descriptions. "
                "Do NOT use the words 'while', 'whereas', 'meanwhile', 'and', or 'but' to join two ideas. "
                "Do NOT combine facts from multiple sources. "
                "One sentence only. No preamble. No caveats. No attribution."
            )},
            {"role": "user", "content": f"SOURCE TEXTS:\n\n{source_text}\n\n---\n\nQUESTION: {prompt}"},
        ],
        temperature=0.4,
        max_tokens=150,
    )
    return response.choices[0].message.content.strip()

def query_with_mistral_directly(prompt: str, source_ids: list[str], uploaded: list[dict], usb_pdfs: list[dict] | None = None) -> str:
    # combine web source IDs with any embedded USB source IDs
    all_source_ids = list(source_ids)
    usb_weights = {}
    if usb_pdfs:
        for pdf in usb_pdfs:
            slot = pdf["slot"]
            usb_source_id = _usb_source_cache.get(slot)
            if usb_source_id:
                all_source_ids.append(usb_source_id)
                usb_weights[slot] = pdf["dial"]

    web_texts = []
    for u in uploaded:
        source_id = u.get("source_id")
        url = u.get("url", "")
        if not source_id:
            continue
        text = fetch_source_content(source_id)
        if text:
            web_texts.append(f"[WEB SOURCE: {url}]\n{text[:3000]}")

    # add USB source content
    for slot, source_id in _usb_source_cache.items():
        if source_id in all_source_ids:
            text = fetch_source_content(source_id)
            if text:
                dial = usb_weights.get(slot, 0)
                web_texts.append(f"[USB SOURCE {slot} | dial={dial:.2f}]\n{text[:3000]}")

    if not web_texts:
        return "No source content could be retrieved."

    return _run_query(prompt, "\n\n---\n\n".join(web_texts))

def find_wikimedia_image(topic: str, country: str = "") -> dict | None:
    queries = [topic] if not country else [f"{topic} {country}", topic]
    for query in queries:
        try:
            response = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={"action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query,
                        "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                        "iiurlwidth": 1200, "format": "json"},
                timeout=20, headers={"User-Agent": "open-notebook-pipeline/1.0"},
            )
            response.raise_for_status()
            data = response.json()
            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                imageinfo = page.get("imageinfo", [{}])[0]
                url = imageinfo.get("url")
                mime = imageinfo.get("mime", "")
                if url and mime.startswith("image/") and "svg" not in mime and not url.lower().endswith((".djvu", ".pdf", ".tiff", ".tif")):
                    title = page.get("title", "").replace("File:", "")
                    description = imageinfo.get("extmetadata", {}).get("ImageDescription", {}).get("value", "")
                    return {
                        "image_url": url,
                        "image_title": title,
                        "image_source": f"https://commons.wikimedia.org/wiki/{page.get('title', '').replace(' ', '_')}",
                        "why_interesting": description or f"Wikimedia Commons: {topic}",
                    }
        except Exception as e:
            print(f"  Wikimedia search failed for '{query}': {e}")
    return None

def watch_for_usb_pdfs(usb_slots: dict) -> None:
    import shutil
    script_dir = os.path.dirname(os.path.abspath(__file__))
    usb_dir = os.path.join(script_dir, "usb_pdfs")
    os.makedirs(usb_dir, exist_ok=True)
    media_dirs = ["/media", "/mnt"]
    drives = []
    for media in media_dirs:
        if os.path.exists(media):
            try:
                for entry in os.listdir(media):
                    drives.append(os.path.join(media, entry))
            except Exception:
                pass
    slot_names = list(usb_slots.keys())
    slot_index = 0
    for drive in drives:
        if slot_index >= len(slot_names):
            break
        try:
            for fname in os.listdir(drive):
                if fname.lower().endswith(".pdf"):
                    slot_name = slot_names[slot_index]
                    dest = os.path.join(usb_dir, f"{slot_name}.pdf")
                    shutil.copy2(os.path.join(drive, fname), dest)
                    print(f"  Copied {fname} from {drive} → {slot_name}")
                    slot_index += 1
                    break
        except Exception as e:
            print(f"  Could not read {drive}: {e}")

def generate_source_summary(notebook_answer: str, uploaded: list[dict], image_result: dict, delta_topic: str) -> str:
    source_list = "\n".join([f"- {u.get('publisher', 'Unknown')}: {u.get('url', '')} — {u.get('why_relevant', '')}" for u in uploaded])
    image_source = image_result.get("image_source", "")
    image_title = image_result.get("image_title", "")
    response = client.chat.complete(
        model="mistral-medium-2505",
        messages=[
            {"role": "system", "content": "You write brief, honest source summaries. For each source, write one sentence. Be concrete and specific. No waffle."},
            {"role": "user", "content": f"TEXT OUTPUT:\n{notebook_answer}\n\nTEXT SOURCES:\n{source_list}\n\nIMAGE: '{image_title}' from {image_source} — related to topic '{delta_topic}'\n\nWrite a brief summary of each source."},
        ],
        temperature=0.3,
        max_tokens=600,
    )
    return response.choices[0].message.content.strip()

def generate_html_summary(topic, country, notebook_answer, uploaded, image_result, delta_topic, source_summary, timestamp):
    image_url = image_result.get("image_url", "")
    image_title = image_result.get("image_title", "")
    image_source = image_result.get("image_source", "")
    source_rows = ""
    for u in uploaded:
        source_rows += f"""<div class="source"><div class="source-publisher">{u.get('publisher', 'Unknown')}</div><a href="{u.get('url', '')}" target="_blank">{u.get('url', '')}</a><p>{u.get('why_relevant', '')}</p></div>"""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{topic} — {country}</title>
<style>body{{font-family:'Georgia',serif;max-width:800px;margin:40px auto;padding:20px;background:#f9f7f2;color:#222;line-height:1.7}}h1{{font-size:1.4em;font-weight:normal;color:#555}}h2{{font-size:1.1em;font-weight:bold;margin-top:40px;border-bottom:1px solid #ddd;padding-bottom:6px}}.meta{{font-size:.85em;color:#888;margin-bottom:30px}}.sentence{{font-size:1.2em;line-height:1.8;background:#fff;padding:20px;border-left:4px solid #c8a96e;margin:20px 0}}.image-block{{margin:30px 0}}.image-block img{{max-width:100%;border:1px solid #ddd}}.image-caption{{font-size:.85em;color:#666;margin-top:6px}}.source{{background:#fff;padding:14px;margin:10px 0;border:1px solid #eee}}.source-publisher{{font-weight:bold;font-size:.9em;color:#555}}.source a{{color:#7a5c2e;font-size:.85em;word-break:break-all}}.source p{{margin:6px 0 0;font-size:.9em;color:#444}}.summary{{background:#fff;padding:20px;border:1px solid #eee;font-size:.95em;white-space:pre-wrap}}.timestamp{{font-size:.8em;color:#aaa;margin-top:40px}}</style>
</head><body>
<h1>{topic} — {country}</h1><div class="meta">Generated {timestamp}</div>
<h2>Output</h2><div class="sentence">{notebook_answer.replace(chr(10), '<br>')}</div>
<h2>Image — {delta_topic}</h2><div class="image-block"><img src="{image_url}" alt="{image_title}"><div class="image-caption">{image_title} — <a href="{image_source}" target="_blank">{image_source}</a></div></div>
<h2>Sources</h2>{source_rows}
<h2>Source Summary</h2><div class="summary">{source_summary}</div>
<div class="timestamp">Run timestamp: {timestamp}</div>
</body></html>"""

def save_html_and_qr(topic, country, notebook_answer, uploaded, image_result, delta_topic, source_summary):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    html_dir = os.path.join(script_dir, "summaries")
    os.makedirs(html_dir, exist_ok=True)
    html_filename = f"summary_{timestamp}.html"
    html_path = os.path.join(html_dir, html_filename)
    html_content = generate_html_summary(topic, country, notebook_answer, uploaded, image_result, delta_topic, source_summary, time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"  HTML summary saved: {html_path}")
    qr_dir = os.path.join(script_dir, "qrcodes")
    os.makedirs(qr_dir, exist_ok=True)
    qr_filename = f"qr_{timestamp}.png"
    qr_path = os.path.join(qr_dir, qr_filename)
    file_url = f"{_server_base_url}/summaries/{html_filename}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(file_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(qr_path)
    print(f"  QR code saved: {qr_path}")
    return {"html_path": html_path, "qr_path": qr_path, "file_url": file_url, "timestamp": timestamp}

def keyboard_thread():
    global _total_rotation, _trigger_hr
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == 'r':
                print("\n[keyboard] Simulating full rotation...")
                with _rotation_lock:
                    _total_rotation = 360.0
            elif ch == 'b':
                print("\r\nSimulating limit switch...")
                _trigger_hr = True
                time.sleep(2)
            elif ch == 'q':
                print("\n[keyboard] Quitting...")
                os._exit(0)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

_kb_thread = threading.Thread(target=keyboard_thread, daemon=True)
_kb_thread.start()

def start_file_server(directory: str, port: int = 8765) -> str:
    import http.server
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    finally:
        s.close()
    handler = http.server.SimpleHTTPRequestHandler
    httpd = http.server.HTTPServer(("", port), handler)
    httpd.allow_reuse_address = True
    thread = threading.Thread(target=httpd.serve_forever)
    thread.daemon = True
    thread.start()
    return f"http://{local_ip}:{port}"

# ---------------- BUILD NOTEBOOK ----------------

def build_notebook_for_topic() -> dict:
    global running
    running = True
    led_reset()

    led_progress(1)
    hw = get_hardware_inputs()
    soft_power_dial = hw["soft_power"]
    overton_dial = hw["overton"]
    semantic_delta = hw["semantic_delta"]

    USB_SLOTS["USB1"]["dial"] = hw["usb1"]
    USB_SLOTS["USB2"]["dial"] = hw["usb2"]
    USB_SLOTS["USB3"]["dial"] = hw["usb3"]
    USB_SLOTS["USB4"]["dial"] = hw["usb4"]
    USB_SLOTS["USB5"]["dial"] = hw["usb5"]

    led_progress(2)
    topic = pick_topic()
    soft_power_data = load_soft_power_csv(INDEX_CSV_PATH)
    country = choose_country(soft_power_data, soft_power_dial, SOFT_POWER_TEMPERATURE)
    overton_label = get_overton_label(overton_dial)
    filter_label = get_content_filter_label(CONTENT_FILTER)
    state_data = get_emotional_state_data(EMOTIONAL_STATE)

    print("\nChecking for USB PDFs...")
    usb_pdfs = []
    for slot, config in USB_SLOTS.items():
        dial = config["dial"]
        if dial > 0 and slot in _usb_source_cache:
            usb_pdfs.append({"slot": slot, "dial": dial, "source_id": _usb_source_cache[slot]})
            print(f"  {slot}: active at dial={dial:.2f}, source_id={_usb_source_cache[slot]}")
    if not usb_pdfs:
        print("  No active USB PDFs")

    print(f"\nTopic:           {topic}")
    print(f"Country:         {country}")
    print(f"Overton window:  {overton_label} ({overton_dial})")
    print(f"Content filter:  {filter_label} ({CONTENT_FILTER})")
    print(f"Emotional state: {EMOTIONAL_STATE} — {state_data['description']}")

    led_progress(3)
    notebook = get_latest_notebook()
    notebook_id = notebook["id"]
    global _notebook_id
    _notebook_id = notebook_id
    notebook_title = notebook.get("title", "(untitled)")
    print(f"Notebook:        {notebook_title} ({notebook_id})\n")

    clear_result = clear_notebook_sources(notebook_id)

    current = led_progress._current if hasattr(led_progress, '_current') else 3
    _led_fill_stop = True
    time.sleep(0.1)
    threading.Thread(target=led_fill_slowly, args=(current, 8, 30), daemon=True).start()

    sources = []
    attempt_count = 0
    while not sources:
        attempt_count += 1
        print(f"  Source search attempt {attempt_count}: {topic} in {country}")
        result = [None]
        def search():
            result[0] = find_sources_with_mistral(country, topic, overton_dial, CONTENT_FILTER, EMOTIONAL_STATE)
        t = threading.Thread(target=search, daemon=True)
        t.start()
        t.join(timeout=90)
        if t.is_alive():
            print(f"  Search timed out, trying new combination...")
        else:
            sources = result[0] or []
        if not sources:
            topic = pick_topic()
            country = choose_country(soft_power_data, soft_power_dial, SOFT_POWER_TEMPERATURE)
            overton_label = get_overton_label(overton_dial)
            print(f"  New topic: {topic}, Country: {country}")

    led_progress._current = 8
    send_led("WHITE:8")

    _led_fill_stop = True
    time.sleep(0.1)
    threading.Thread(target=led_fill_slowly, args=(8, 11, 20), daemon=True).start()
    uploaded = []
    skipped = []

    for source in sources:
        url = source["url"]
        is_wikipedia = "wikipedia.org/wiki/" in url
        if is_wikipedia:
            wiki_text = get_wikipedia_summary(url)
            if not wiki_text:
                skipped.append({"url": url, "publisher": source.get("publisher"), "reason_skipped": "wikipedia API returned no content"})
                continue
            result = add_text_source(notebook_id, source.get("title", url), wiki_text)
        else:
            ok, reason = url_looks_fetchable(url)
            if not ok:
                skipped.append({"url": url, "publisher": source.get("publisher"), "reason_skipped": reason})
                continue
            result = add_link_source(notebook_id, url)
        source_id = result.get("id")
        uploaded.append({"url": url, "source_id": source_id, "title": result.get("title"), "publisher": source.get("publisher"), "why_credible": source.get("why_credible"), "why_relevant": source.get("why_relevant")})

    led_progress._current = 11
    send_led("WHITE:11")

    if not uploaded:
        print("No sources uploaded, retrying...")
        running = False
        return build_notebook_for_topic()
    
    _led_fill_stop = True
    time.sleep(0.1)
    threading.Thread(target=led_fill_slowly, args=(11, 13, 30), daemon=True).start()
    print("\nWaiting for sources to finish processing...")
    for u in uploaded:
        if u["source_id"]:
            wait_for_source_processing(u["source_id"])
    led_progress._current = 13
    send_led("WHITE:13")

    _led_fill_stop = True
    time.sleep(0.1)
    threading.Thread(target=led_fill_slowly, args=(13, 15, 20), daemon=True).start()
    notebook_prompt = get_notebook_prompt(EMOTIONAL_STATE, CONTENT_FILTER)
    print("\nQuerying notebook...")
    source_ids = [u["source_id"] for u in uploaded if u["source_id"]]
    notebook_answer = query_with_mistral_directly(notebook_prompt, source_ids, uploaded, usb_pdfs)
    print("\n" + "=" * 60)
    print(notebook_answer)
    print("=" * 60 + "\n")
    led_progress._current = 15
    send_led("WHITE:15")

    image_result = {}
    image_path = None
    delta_topic = None
    specific_subject = None

    for attempt in range(10):
        delta_topic = generate_delta_topic(topic, semantic_delta)
        print(f"Semantic delta ({semantic_delta}): '{topic}' → '{delta_topic}'")
        image_result = find_wikimedia_image(delta_topic) or {}
        image_url = image_result.get("image_url")
        if image_url:
            image_path = download_image(image_url)
            if image_path:
                break
        print(f"  No image found, trying a new delta topic...")

    image_result["image_local_path"] = image_path
    image_result["specific_subject"] = specific_subject
    thermal_path = process_image_for_thermal(image_path) if image_path else None
    image_result["thermal_path"] = thermal_path

    print("\nGenerating source summary...")
    source_summary = generate_source_summary(notebook_answer, uploaded, image_result, delta_topic)
    print("Generating HTML and QR code...")
    qr_result = save_html_and_qr(topic, country, notebook_answer, uploaded, image_result, delta_topic, source_summary)
    print(f"  Open in browser: {qr_result['file_url']}")

    led_complete()
    print("\nPrinting...")
    send_print(notebook_answer, image_path=thermal_path, qr_url=qr_result['file_url'])
    send_led("OFF")

    running = False

    return {
        "created": True,
        "country": country,
        "topic": topic,
        "overton_label": overton_label,
        "overton_dial": overton_dial,
        "content_filter_label": filter_label,
        "content_filter": CONTENT_FILTER,
        "emotional_state": EMOTIONAL_STATE,
        "notebook_id": notebook_id,
        "notebook_title": notebook_title,
        "sources_found": len(sources),
        "sources_uploaded": len(uploaded),
        "sources_skipped": len(skipped),
        "notebook_answer": notebook_answer,
        "semantic_delta": semantic_delta,
        "delta_topic": delta_topic,
        "thermal_path": thermal_path,
        "html_path": qr_result["html_path"],
        "qr_path": qr_result["qr_path"],
    }

# Start file server
_server_base_url = start_file_server(os.path.join(os.path.dirname(os.path.abspath(__file__))))
print(f"File server running at: {_server_base_url}")

# Start serial reader thread
_reader_thread = threading.Thread(target=serial_reader_thread, daemon=True)
_reader_thread.start()

_usb_thread = threading.Thread(target=usb_monitor_thread, daemon=True)
_usb_thread.start()

time.sleep(2)
print("Waiting for full rotation to trigger run...")

# pre-fetch notebook ID so USB embedding can start immediately
try:
    _notebook = get_latest_notebook()
    _notebook_id = _notebook["id"]
    print(f"Notebook ready: {_notebook_id}")
except Exception as e:
    print(f"Could not pre-fetch notebook: {e}")

# ---------------- MAIN LOOP ----------------

if __name__ == "__main__":
    load_state()
    oled_counter = 0
    while True:
        with _rotation_lock:
            rotation = _total_rotation

        if _trigger_hr and not running and not _hr_cooldown:
            _trigger_hr = False
            _hr_cooldown = True
            print("Limit switch triggered — reading emotional state...")
            state = read_emotional_state(timeout=10)
            if state:
                EMOTIONAL_STATE = state
                print(f"Emotional state set to: {EMOTIONAL_STATE}")
                save_state()
            time.sleep(3)
            _hr_cooldown = False

        if abs(rotation) >= 360 and not running:
            print(f"\nFull rotation detected! Running...")
            with _rotation_lock:
                _total_rotation = 0.0
                _last_angle = None
            result = build_notebook_for_topic()
            print(f"Done! Topic: {result.get('topic')}, Country: {result.get('country')}")
            led_reset()

        if not running:
            oled_counter += 1
            if oled_counter >= 1:
                update_displays()
                oled_counter = 0

        time.sleep(0.05)