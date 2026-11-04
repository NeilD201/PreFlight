# Demo preset — ModularKeyboard wrong

This is a modular mechanical keyboard ("ModularKeyboard wrong" demo) built
around an RP2040 microcontroller (U2) with external QSPI flash (U5 W25Q128JVS).

Verified hardware (read from the schematic and PCB — do not re-ask this):
- 82-key switch matrix with per-key diodes (Switch_Matrix sheet), scanned via
  I2C GPIO expansion (PCF8574A U4 behind a TCA9548A I2C mux U6).
- Two rotary encoders with push switches (SW1, SW2).
- Per-key RGB from a long SK6812MINI addressable-LED chain (head D82),
  driven through level shifter U8.
- USB-C connector (J1) for data and power.
- Power from LD1117 regulators (U3, U7); crystal Y1 for the MCU clock.
- SWD debug header (J2); seven I2C expansion ports (J3–J9, each
  3V3/SDA/SCL/GND on its own bus, no driver needed unless a module is attached).

Assumptions for firmware — marked, change freely, never treated as measured:
- Encoders: volume up/down on rotation, mute on press. (Confirm per encoder.)
- RGB: short boot animation, then per-key lighting. (Confirm.)
- Layout: standard USB HID keyboard; matrix positions map left-to-right,
  top-to-bottom. (Confirm.)
- Expansion ports J3–J9: idle unless a module is attached. (Confirm if used.)

Design requirements PreFlight should apply:
- The board must enumerate as a standard USB HID keyboard.
- 3.3 V logic throughout; expansion ports are 3.3 V I2C only.
- Switching the encoder or keys must never reset the MCU or drop USB.
