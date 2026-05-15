#include <Wire.h>
#include <Adafruit_NeoPixel.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ADS1X15.h>

// ---- LED STRIP ----
#define LED_PIN 5
#define LED_COUNT 60
#define BRIGHTNESS 38  // 15% of 255
Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

// ---- OLED ----
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define TCA_ADDR 0x70
#define AS5600_ADDR 0x36
#define MAX30102_ADDR 0x57

Adafruit_SSD1306 oled0(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
Adafruit_SSD1306 oled1(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
Adafruit_SSD1306 oled2(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// ---- ADS1115 ----
Adafruit_ADS1115 ads;

// ---- SWITCH PINS ----
#define REED1_PIN 2
#define REED2_PIN 3
#define REED3_PIN 4
#define SHUTDOWN_PIN 6
#define LIMIT_PIN 12

// ---- STATE ----
bool oledInitialised[3] = {false, false, false};
String oledLabels[3] = {"Semantic", "Overton", "Soft Power"};
float oledValues[3] = {0.0, 0.0, 0.0};
volatile bool i2cBusy = false;

// ---- TCA SELECT ----
void selectTCA(uint8_t channel) {
  Wire.beginTransmission(TCA_ADDR);
  Wire.write(0x00);
  Wire.endTransmission();
  delay(5);
  Wire.beginTransmission(TCA_ADDR);
  Wire.write(1 << channel);
  Wire.endTransmission();
  delay(5);
}

// ---- MAX30102 REGISTER ACCESS ----
void writeReg(uint8_t reg, uint8_t val) {
  selectTCA(6);
  Wire.beginTransmission(MAX30102_ADDR);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
  delay(5);
}

uint8_t readReg(uint8_t reg) {
  selectTCA(6);
  Wire.beginTransmission(MAX30102_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(MAX30102_ADDR, 1);
  return Wire.read();
}

// ---- INIT MAX30102 ----
void initMAX30102() {
  selectTCA(6);
  writeReg(0x09, 0x40); // reset
  delay(500);           // wait for reset to complete
  selectTCA(6);
  writeReg(0x08, 0x4F); // FIFO: 4 sample avg, rollover on
  writeReg(0x09, 0x03); // SpO2 mode
  writeReg(0x0A, 0x27); // SPO2: 4096nA, 100Hz, 411us
  writeReg(0x0C, 0x24); // Red LED amplitude
  writeReg(0x0D, 0x24); // IR LED amplitude
}

// ---- STREAM MAX30102 RAW DATA ----
void streamMAX30102Raw(int samples) {
  i2cBusy = true;
  initMAX30102();
  delay(200);

  // settle for 3 seconds, drain FIFO
  unsigned long settleStart = millis();
  int lastSettlePing = 0;
  while (millis() - settleStart < 3000) {
    if ((int)((millis() - settleStart) / 500) > lastSettlePing) {
      Serial.println("SETTLING");
      lastSettlePing++;
    }
    uint8_t wp = readReg(0x04);
    uint8_t rp = readReg(0x06);
    if (wp != rp) {
      selectTCA(6);
      Wire.beginTransmission(MAX30102_ADDR);
      Wire.write(0x07);
      Wire.endTransmission(false);
      Wire.requestFrom(MAX30102_ADDR, 6);
      for (int i = 0; i < 6; i++) Wire.read();
    }
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.startsWith("LED:")) {
        handleLED(cmd.substring(4));
      }
    }
    delay(10);
  }

  int count = 0;
  unsigned long startTime = millis();

  while (count < samples && millis() - startTime < 7000) {
    uint8_t writePtr = readReg(0x04);
    uint8_t readPtr  = readReg(0x06);
    int numAvailable = (writePtr - readPtr) & 0x1F;

    for (int i = 0; i < numAvailable && count < samples; i++) {
      selectTCA(6);
      Wire.beginTransmission(MAX30102_ADDR);
      Wire.write(0x07);
      Wire.endTransmission(false);
      Wire.requestFrom(MAX30102_ADDR, 6);

      uint32_t red = ((uint32_t)(Wire.read() & 0x03) << 16) | ((uint32_t)Wire.read() << 8) | Wire.read();
      uint32_t ir  = ((uint32_t)(Wire.read() & 0x03) << 16) | ((uint32_t)Wire.read() << 8) | Wire.read();

      Serial.print("RAW:");
      Serial.print(red);
      Serial.print(":");
      Serial.print(ir);
      Serial.print(":");
      Serial.println(millis() - startTime);
      count++;
    }

    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.startsWith("LED:")) {
        handleLED(cmd.substring(4));
      }
    }

    delay(10);
  }

  Serial.println("HR_DONE");
  strip.clear();
  strip.show();
  i2cBusy = false;
}

// ---- INIT OLEDS ----
void initOLEDs() {
  selectTCA(2);
  if (oled0.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    oledInitialised[0] = true;
    oled0.clearDisplay();
    oled0.display();
  }
  selectTCA(3);
  if (oled1.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    oledInitialised[1] = true;
    oled1.clearDisplay();
    oled1.display();
  }
  selectTCA(4);
  if (oled2.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    oledInitialised[2] = true;
    oled2.clearDisplay();
    oled2.display();
  }
}

// ---- DRAW OLED ----
void drawOLED(uint8_t idx, String label, float value) {
  uint8_t channel = idx + 2;
  selectTCA(channel);
  Adafruit_SSD1306* display;
  if (idx == 0) display = &oled0;
  else if (idx == 1) display = &oled1;
  else display = &oled2;
  if (!oledInitialised[idx]) return;
  display->clearDisplay();
  display->setTextSize(1);
  display->setTextColor(SSD1306_WHITE);
  display->setCursor(0, 10);
  display->println(label);
  display->setTextSize(2);
  display->setCursor(0, 35);
  display->println(String(value, 2));
  display->display();
}

// ---- UPDATE ALL OLEDS ----
void updateOLEDs() {
  for (int i = 0; i < 3; i++) {
    drawOLED(i, oledLabels[i], oledValues[i]);
  }
}

// ---- READ AS5600 ----
float readAngle() {
  selectTCA(7);
  Wire.beginTransmission(AS5600_ADDR);
  Wire.write(0x0E);
  Wire.endTransmission(false);
  Wire.requestFrom(AS5600_ADDR, 2);
  if (Wire.available() < 2) return -1;
  uint8_t high = Wire.read();
  uint8_t low = Wire.read();
  int raw = ((high & 0x0F) << 8) | low;
  return raw * 360.0 / 4096.0;
}

// ---- READ ADS1115 POTS ----
float readADSPot(uint8_t channel) {
  selectTCA(5);
  int16_t raw = ads.readADC_SingleEnded(channel);
  return constrain((float)raw / 26400.0, 0.0, 1.0);
}

// ---- LED CONTROL ----
void setMirroredLED(int pos, uint32_t color) {
  // pos is 0-14
  strip.setPixelColor(pos, color);          // strip 1 forward
  strip.setPixelColor(29 - pos, color);     // strip 2 mirrored
  strip.setPixelColor(30 + pos, color);     // strip 3 forward
  strip.setPixelColor(59 - pos, color);     // strip 4 mirrored
}

void handleLED(String cmd) {
  if (cmd == "OFF") {
    strip.clear();
    strip.show();
    return;
  }
  int colonIdx = cmd.indexOf(':');
  if (colonIdx == -1) return;
  String colour = cmd.substring(0, colonIdx);
  int count = cmd.substring(colonIdx + 1).toInt();
  count = constrain(count, 0, 15);
  strip.clear();
  uint32_t col;
  if (colour == "RED") col = strip.Color(255, 0, 0);
  else if (colour == "WHITE") col = strip.Color(255, 255, 255);
  else if (colour == "GREEN") col = strip.Color(0, 255, 0);
  else if (colour == "BLUE") col = strip.Color(0, 0, 255);
  else return;
  for (int i = 0; i < count; i++) {
    setMirroredLED(i, col);
  }
  strip.show();
}

// ---- HANDLE OLED COMMAND ----
// Format: OLED:idx:label:value
void handleOLED(String cmd) {
  int c1 = cmd.indexOf(':');
  if (c1 == -1) return;
  int idx = cmd.substring(0, c1).toInt();
  if (idx < 0 || idx > 2) return;
  int c2 = cmd.indexOf(':', c1 + 1);
  if (c2 == -1) return;
  String label = cmd.substring(c1 + 1, c2);
  if (label.length() == 0 || label.length() > 20) return;  // add this
  float value = cmd.substring(c2 + 1).toFloat();
  oledLabels[idx] = label;
  oledValues[idx] = value;
  drawOLED(idx, label, value);
}

// ---- SETUP ----
void setup() {
  Serial.begin(115200);
  Wire.begin();

  strip.begin();
  strip.setBrightness(BRIGHTNESS);
  strip.clear();
  strip.show();

  pinMode(REED1_PIN, INPUT_PULLUP);
  pinMode(REED2_PIN, INPUT_PULLUP);
  pinMode(REED3_PIN, INPUT_PULLUP);
  pinMode(SHUTDOWN_PIN, INPUT_PULLUP);
  pinMode(LIMIT_PIN, INPUT_PULLUP);

  initOLEDs();

  selectTCA(5);
  ads.begin(0x48);

  initMAX30102();

  delay(500);
  Serial.setTimeout(50);
  Serial.println("READY");
}

// ---- LOOP ----
unsigned long lastPotSend = 0;
unsigned long lastAngleSend = 0;
unsigned long lastOLEDUpdate = 0;

void loop() {
  unsigned long now = millis();

  if (!i2cBusy && now - lastPotSend > 35) {
    lastPotSend = now;
    float pot0 = readADSPot(1);
    float pot1 = readADSPot(2);
    float pot2 = readADSPot(3);
    float lin0 = analogRead(A0) / 1023.0;
    float lin1 = analogRead(A1) / 1023.0;
    float lin2 = analogRead(A2) / 1023.0;
    float lin3 = analogRead(A3) / 1023.0;
    float lin4 = analogRead(A4) / 1023.0;
    Serial.print("POTS:");
    Serial.print(pot0, 3); Serial.print(":");
    Serial.print(pot1, 3); Serial.print(":");
    Serial.print(pot2, 3); Serial.print(":");
    Serial.print(lin0, 3); Serial.print(":");
    Serial.print(lin1, 3); Serial.print(":");
    Serial.print(lin2, 3); Serial.print(":");
    Serial.print(lin3, 3); Serial.print(":");
    Serial.println(lin4, 3);
  }

  if (!i2cBusy && now - lastAngleSend > 50) {
    lastAngleSend = now;
    float angle = readAngle();
    if (angle >= 0) {
      Serial.print("ANGLE:");
      Serial.println(angle, 2);
    }
  }


  if (digitalRead(REED1_PIN) == LOW) {
    Serial.println("REED:1");
    delay(300);
  }
  if (digitalRead(REED2_PIN) == LOW) {
    Serial.println("REED:2");
    delay(300);
  }
  if (digitalRead(REED3_PIN) == LOW) {
    Serial.println("REED:3");
    delay(300);
  }
  if (digitalRead(LIMIT_PIN) == LOW) {
    Serial.println("LIMIT:1");
    delay(300);
  }
  if (digitalRead(SHUTDOWN_PIN) == LOW) {
    Serial.println("SHUTDOWN");
    delay(1000);
  }

  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("LED:")) {
      handleLED(cmd.substring(4));
    } else if (cmd.startsWith("OLED:")) {
      handleOLED(cmd.substring(5));
    } else if (cmd == "READ_HR") {
      streamMAX30102Raw(500);
    }
  }
}
