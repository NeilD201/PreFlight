# Demo preset — ModularKeyboard wrong (WenneBoard, faulty revision)

WenneBoard is a custom modular 75% ISO mechanical keyboard ("~82-key" class:
81 fitted keyswitches SW3–SW83 plus 2 rotary encoders) built around an RP2040
microcontroller (U2) with external QSPI flash (U5 W25Q128JVS, 128 Mbit).
Hot-swappable MX-compatible switches and the desktop configuration software
are the developer's product statements, not facts read from these files.

## Verified hardware (read from this schematic and PCB — do not re-ask)

- Matrix: 6 rows R0–R5 (RP2040 GPIO19–24) x 15 columns C0–C14
  (GPIO4–7 then GPIO8–18). Every switch has a series diode, anode toward the
  switch, cathode toward the row (e.g. D1: A→switch→C0, K→R0).
- Encoders SW1/SW2 (quadrature A/B + push S2, common C/S1 grounded) are read
  by a PCF8574A I2C expander (U4, address pins to GND): P0–P2 → SW1,
  P4–P6 → SW2. U4 INT → RP2040 GPIO3; main bus SDA/SCL → GPIO0/GPIO1 with
  4.7 kΩ pull-ups R16/R17 to +3V3.
- Expansion J3–J9 (4-pin 2.54 mm) behind a TCA9548A mux (U6, address to GND):
  pin 1 = +3.3V, pin 2 = SDx, pin 3 = SCx, pin 4 = GND. Buses used:
  J3 SD5/SC5, J4 SD0/SC0, J5 SD1/SC1, J6 SD2/SC2, J7 SD3/SC3, J8 SD4/SC4,
  J9 SD6/SC6; SD7/SC7 unconnected. Each switched bus has 4.7 kΩ pull-ups to
  the +3.3V rail (e.g. R43 on SD2). Ports are 3.3 V I2C only.
- RGB: 81x SK6812MINI (D82–D162), VDD = +5V, data RP2040 GPIO2 → SN74LV1T34
  level shifter (U8, VCC = +5V) → 500 Ω R10 → D82 DIN; second 500 Ω (R11)
  mid-chain between D109 and D117.
- USB-C J1: VBUS → +5V rail; D+/D- via 27 Ω R4/R5 to RP2040 USB_DP/DM;
  SBU1 unconnected. CC1/CC2 pulled by R2/R3 — on THIS board 10 kΩ to GND,
  out of USB-C spec (the known fault; the corrected board uses 5.1 kΩ).
- Power: +5V bulk C15 100 µF. LD1117 U3 (+5V→+3V3) and U7 (+5V→+3.3V) are
  separate rails. RP2040 VREG_IN = +3V3, internal regulator out to +1V1 for
  DVDD; ADC supply filtered separately (FB1 600 Ω from +3V3). Crystal Y1
  with 22 pF C1/C2; TESTEN grounded.
- Buttons: RST1 pulls ~RESET (10 kΩ R6 pull-up to +3V3, also RP2040 RUN);
  BOOT1 plus R7 1 kΩ / R8,R9 5.1 kΩ form the QSPI-CS BOOTSEL network on
  Q_SEL (flash pin 1, RP2040 QSPI_SS). SWD header J2: 1 RESET, 2 +3V3,
  3 GND, 4 SWC, 5 SWD.
- Indicator LEDs D168–D170 (anode via R12–R14, cathode GND): on THIS board
  R12–R14 are 10 kΩ, giving ~0.13 mA — too dim (known fault; corrected
  board uses 500 Ω). Bulk caps C105/C106 are 100 µF here (known fault;
  corrected: C105 100 nF, C106 10 µF). No USB ESD device and no VBUS fuse
  on this revision (corrected board adds SRV05-4 U1, D165/D167, F1, C103).

## Developer requirements (apply these, do not treat as measured)

- Must enumerate as a standard USB HID keyboard; key/encoder input must
  never reset the MCU or drop USB.
- 3.3 V logic throughout; expansion ports are 3.3 V I2C only — no 5 V.
- Modules are hypothetical: no module hardware is implemented in these
  files. Expect idle buses (only pull-ups and mux present) until a module
  is attached; confirm any module's power draw and address before approval.

## Firmware assumptions (intended, NOT in the KiCad files — confirm)

- QMK-based firmware with custom desktop configuration software (developer
  statement; no firmware source is bundled here).
- Encoders: volume up/down on rotation, mute on press. RGB: short boot
  animation, then per-key lighting. Standard USB HID layout, matrix mapped
  left-to-right, top-to-bottom.
- Open questions: exact keymap; encoder step behavior; RGB current budget
  on +5V; module protocol/addressing; desktop-software message format.
