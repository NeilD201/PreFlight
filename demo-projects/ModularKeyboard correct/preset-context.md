# Demo preset — ModularKeyboard correct (WenneBoard, fixed reference)

WenneBoard is a custom modular 75% ISO mechanical keyboard ("~82-key" class:
81 fitted keyswitches SW3–SW83 plus 2 rotary encoders) built around an RP2040
microcontroller (U2) with external QSPI flash (U5 W25Q128JVS, 128 Mbit).
Hot-swappable MX-compatible switches and the desktop configuration software
are the developer's product statements, not facts read from these files.

## Verified hardware (read from this schematic and PCB — do not re-ask)

Same architecture as the faulty board: 6x15 diode matrix (rows R0–R5 on
GPIO19–24, columns C0–C14 on GPIO4–18, anode toward switch), SW1/SW2 via
PCF8574A U4 (INT → GPIO3), J3–J9 expansion on TCA9548A buses (pin 1 +3.3V,
2 SDx, 3 SCx, 4 GND; SD7/SC7 unused), 81x SK6812MINI via U8 level shifter,
RP2040 clocks/reset/SWD as on the faulty board, separate +3V3 (U3) and
+3.3V (U7) rails, +5V bulk C15 100 µF, FB1-filtered ADC supply.

Fixes verified present on THIS board (compare against "ModularKeyboard
wrong" to see each resolved finding disappear):
- R2/R3 = 5.1 kΩ CC pull-downs to GND (USB-C compliant).
- R12–R14 = 500 Ω LED series resistors.
- C105 = 100 nF, C106 = 10 µF; extra C103 100 nF on +3.3V.
- U1 SRV05-4 USB ESD protection (D+/D-, VP = +5V, VN = GND).
- D165/D167 zeners on SC0/SD0 to GND; F1 fuse in series with J1 VBUS.

## Developer requirements (apply these, do not treat as measured)

- Must enumerate as a standard USB HID keyboard; key/encoder input must
  never reset the MCU or drop USB.
- 3.3 V logic throughout; expansion ports are 3.3 V I2C only — no 5 V.
- Modules are hypothetical: no module hardware is implemented in these
  files. Expect idle buses until a module is attached; confirm any
  module's power draw and address before approval.

## Firmware assumptions (intended, NOT in the KiCad files — confirm)

- QMK-based firmware with custom desktop configuration software (developer
  statement; no firmware source is bundled here).
- Encoders: volume up/down on rotation, mute on press. RGB: short boot
  animation, then per-key lighting. Standard USB HID layout, matrix mapped
  left-to-right, top-to-bottom.
- Open questions: exact keymap; encoder step behavior; RGB current budget
  on +5V; module protocol/addressing; desktop-software message format.
