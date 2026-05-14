from escpos.printer import Serial
from PIL import Image

p = Serial('/dev/ttyUSB0', baudrate=9600, timeout=5)

# Test text
p.set(align='center', bold=True, height=2, width=2)
p.text("UNBLOCKER\n")
p.set(align='center', bold=False, height=1, width=1)
p.text("Printer test\n")
p.text("Hello from the Jetson!\n")
p.text("\n")

# Test image
img = Image.open("/home/orin/unblocker/thermal/delta_image_1778683511_thermal.png").convert("RGB")
p.image(img)

p.text("\n\n\n")
p.cut()