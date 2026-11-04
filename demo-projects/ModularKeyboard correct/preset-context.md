# Demo preset — ModularKeyboard correct

This is the corrected reference board for the modular mechanical keyboard
demo ("ModularKeyboard correct"). Same RP2040 architecture as the faulty
board: U2 MCU, W25Q128JVS flash (U5), 82-key matrix with per-key diodes,
two rotary encoders (SW1, SW2), SK6812MINI RGB chain via U8, USB-C (J1),
LD1117 regulators (U3, U7), SWD header (J2), and seven I2C expansion
ports (J3–J9).

Compare it against "ModularKeyboard wrong" to see resolved findings:
grouped LED-resistor, capacitor, regulator, USB-protection and signal-swap
issues should be absent or reduced here.

Same firmware assumptions as the faulty board (confirm before generating):
volume up/down on rotation, mute on press; boot animation then per-key
lighting; standard USB HID keyboard; 3.3 V logic throughout.
