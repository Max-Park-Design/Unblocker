# Unblocker

## Software Setup

### Requirements
- Jetson Orin Nano running Ubuntu
- Python 3.10+
- Docker + Docker Compose
- Mistral AI API key — get one at [console.mistral.ai](https://console.mistral.ai)

### 1. Clone the repo
```bash
git clone https://github.com/Max-Park-Design/Unblocker.git
cd Unblocker
```

### 2. Install Python dependencies
```bash
python3 -m pip install smbus2 pillow requests mistralai==1.2.5 pyserial qrcode python-escpos PyPDF2 pyudev
```

### 3. Fix Docker networking on Jetson
The Jetson kernel doesn't support the iptable_raw module, so add this to `/etc/docker/daemon.json`:
```json
{
    "iptables": false,
    "runtimes": {
        "nvidia": {
            "args": [],
            "path": "nvidia-container-runtime"
        }
    }
}
```

Then fix container internet access (replace `wlP1p1s0` with your WiFi interface from `ip link show`):
```bash
sudo iptables -t nat -A POSTROUTING -o wlP1p1s0 -j MASQUERADE
sudo iptables -P FORWARD ACCEPT
sudo apt install iptables-persistent -y
sudo netfilter-persistent save
sudo systemctl restart docker
```

### 4. Start Open Notebook
```bash
cd ~
git clone https://github.com/lfnovo/open-notebook.git
cd open-notebook
cp .env.example .env
```

Edit `.env` and set `OPEN_NOTEBOOK_ENCRYPTION_KEY=any-random-string`, then:
```bash
docker compose up -d
```

### 5. Add your Mistral API key
Open `http://<jetson-ip>:8502` in a browser, go to Settings → API Keys, and add your Mistral key. Then create at least one notebook.

### 6. Upload the Arduino sketch
```bash
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
export PATH=$PATH:~/bin
arduino-cli core update-index
arduino-cli core install arduino:megaavr
arduino-cli lib install "Adafruit NeoPixel" "Adafruit SSD1306" "Adafruit GFX Library" "Adafruit BusIO" "Adafruit ADS1X15"
arduino-cli compile --fqbn arduino:megaavr:nona4809 unblocker.ino
arduino-cli upload --fqbn arduino:megaavr:nona4809 --port /dev/ttyACM0 unblocker.ino
```

### 7. Allow passwordless USB mounting
```bash
sudo visudo
```
Add: `YOUR_USERNAME ALL=(ALL) NOPASSWD: /bin/mount, /bin/umount`

### 8. Map your USB hub ports
Plug a drive into each slot one at a time and run:
```bash
python3 -c "
import pyudev
context = pyudev.Context()
for device in context.list_devices(subsystem='block', DEVTYPE='partition'):
    print(device.get('ID_PATH'), device.device_node)
"
```
Update `USB_PORT_MAP` in `unblocker.py` with the paths for each slot.

### 9. Run
```bash
sudo chmod 666 /dev/ttyACM0 /dev/ttyUSB0
python3 unblocker.py
```