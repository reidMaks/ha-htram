---
name: gd32-pins-power
description: The GD32 pin map, the rules for touching a pin whose function is not yet established, and how battery voltage, charging and USB presence are actually measured. Trigger terms (uk) - нога, пін, розпіновка, що робить пін, заряд, зарядка, батарея, акумулятор, напруга, живлення, USB, не заряджається, відсоток заряду.
---

# Pins, battery and power

## The discipline

**Do not drive a pin whose function you have not established.** Two of this
project's worst bugs were exactly that, and neither announced itself:

- Pulsing `PA8` "to reset the SHT30" was in fact **resetting the ESP32** on
  every boot. `PA8` is the ESP's RESET/EN line; the factory firmware never
  drives it. Leave it as INPUT. The SHT30 needs only `PB6`/`PB7`.
- Holding `PB2` HIGH **blocked battery charging for two days**. Drive it LOW.
  Its real function is still unknown -- all that is established is that HIGH
  stops the charger and LOW breaks nothing we use. The trap was a too-broad
  conclusion: the bench proved `PB2` gates neither the SHT30 nor the ADC
  divider, and that got read as "the pin does nothing". Nobody had asked it
  about the charger.

The way to establish a pin is to compare against the factory image running on
live hardware over SWD, then confirm with an SRAM probe -- see `htram-bench`.

## Map

| Pin | Role |
| --- | --- |
| `PA0` | button, active-HIGH, pulled to GND on the board |
| `PA1` | LED anode rail via P-MOSFET (HIGH = LEDs powered) |
| `PA2`/`PA3` | USART1 to the ESP32 (TX/RX) |
| `PA8` | **ESP32 RESET/EN** -- leave as INPUT |
| `PA9`/`PA10` | USART0 to the CO2 sensor, 9600, Modbus |
| `PA15` | CHRG from the charger, **active LOW**, open-drain |
| `PB0` | buzzer (TIMER2_CH2) |
| `PB1` | battery voltage, `ADC_IN9` |
| `PB2` | unknown; HIGH blocks charging, we hold it LOW |
| `PB3` | ESP32 power rail |
| `PB4` / `PB5` / `PC14` | yellow LED / red LEDs / green LEDs |
| `PB6`/`PB7` | bit-banged I2C to the SHT30 |
| `PB8` | display backlight |
| `PB9` / `PB11` | CO2 sensor power / 5 V boost enable |
| `PB12`..`PB15` | display, see `htram-display` |
| `PC13` | USB VBUS present |
| `PC15` | main DC-DC latch (does **not** affect charging -- A/B tested) |
| `PF7` | display panel power |

`PF7` and `PB3` are separate gates, not one master enable: `PF7` sits upstream
(without it nothing comes up at all), `PB3` gates the ESP rail downstream. So
the panel can be lit while the ESP is unpowered -- which is exactly how the
standby charge screen works.

## Battery

Voltage uses the factory constant, validated against a reference unit's own
telemetry -- not a nominal 2:1 divider at 3.3 V:

```c
mv = (raw * 3275) >> 11;
```

State of charge is a piecewise-linear curve in `htram_gd32.cpp`, topping out at
**4180 mV = 100 %**. The nominal 4200 is wrong for this purpose: the charger
ends its CV phase when the taper current falls, and a full pack reads back
around 4184 mV, so a curve topping at 4200 would sit at 99 % forever.

## Charging semantics

`CHRG` is active LOW. We report charging as `usb && !chrg_pin`. Reading it the
other way round is a mistake this project already made once, and it claimed
"Charging" for a whole day while the cell gained nothing.

When USB is present, `CHRG` is released and the voltage is ~4.18 V, the pack is
**full** -- charge terminated normally. Nothing in our firmware enables or
disables charging at runtime: `PB2` (LOW) and `PC15` (HIGH) are set once in
`periph_init()` and never touched again.
