import serial
import time

s = serial.Serial()
s.port = '/dev/ttyACM0'
s.baudrate = 115200
s.timeout = 1
s.dtr = False
s.open()
time.sleep(2)
s.reset_input_buffer()

print("Sending READ_HR...")
s.write(b"READ_HR\n")

red_vals = []
ir_vals = []
timestamps = []

while True:
    line = s.readline().decode('utf-8').strip()
    if line.startswith("RAW:"):
        parts = line.split(":")
        if len(parts) == 4:
            red_vals.append(int(parts[1]))
            ir_vals.append(int(parts[2]))
            timestamps.append(int(parts[3]))
            print(f"  {len(ir_vals)}: red={parts[1]} ir={parts[2]} t={parts[3]}ms")
    elif line == "HR_DONE":
        print("HR_DONE")
        break

print(f"\nIR avg: {sum(ir_vals)//len(ir_vals)}")
print(f"IR range: {min(ir_vals)} - {max(ir_vals)}")
print(f"Total duration: {timestamps[-1]}ms")

# find peaks
ir_mean = sum(ir_vals) / len(ir_vals)
ir_ac = max(ir_vals) - min(ir_vals)
threshold = ir_mean - (ir_ac * 0.3)
print(f"Peak threshold: {threshold:.0f}")

peaks = []
for i in range(1, len(ir_vals) - 1):
    if ir_vals[i] > threshold and ir_vals[i] > ir_vals[i-1] and ir_vals[i] > ir_vals[i+1]:
        if not peaks or timestamps[i] - timestamps[peaks[-1]] > 600:
            peaks.append(i)

print(f"Peaks found: {len(peaks)} at indices {peaks}")
if len(peaks) >= 2:
    total_time_s = (timestamps[peaks[-1]] - timestamps[peaks[0]]) / 1000.0
    hr = (len(peaks) - 1) / total_time_s * 60.0
    print(f"HR: {hr:.0f} bpm")
else:
    print("Not enough peaks for HR")
    hr = None

# spo2 using windowed calculation
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

if r_vals:
    r = sum(r_vals) / len(r_vals)
    spo2 = 104 - 17 * r
    print(f"Raw SpO2: {spo2:.1f}% (avg of {len(r_vals)} windows, r={r:.3f})")
    if 90 <= spo2 <= 100:
        print(f"SpO2 valid: {spo2:.1f}%")
    else:
        print(f"SpO2 out of range ({spo2:.1f}%) — bad placement, ignoring")
        spo2 = None
else:
    print("Could not calculate SpO2")
    spo2 = None