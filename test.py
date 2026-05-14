import serial
import time
ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=2)
time.sleep(3)
for _ in range(5):
    line = ser.readline().decode().strip()
    if line:
        print(line)