# Стенд розробки (hardware-in-the-loop)

Мета: дати Claude Code (через Bash на цій машині) можливість **скидати/прошивати GD32, ганяти SRAM-проби периферії та слати команди на міжчиповий UART**, поки ESP32 доступний бездротово.

## 1. Топологія

```
   Linux-хост (Claude Code: openocd + pyserial)
        │ USB
        ▼
   Raspberry Pi Pico  (debugprobe / CMSIS-DAP)
     ├── SWD ────────────► GD32  (reset/halt/flash/SRAM-run)
     └── UART-міст ──────► GD32 USART1 (роль "ESP": слати команди, читати вивід)

   ESP32-WROOM-32E: прошитий мінімальним ESPHome (esphome/htram.yaml),
     GPIO16/17 НЕ сконфігуровані → high-Z, не заважає Pico на USART1.
     Доступ до ESP — по Wi-Fi (API/OTA/логи).

   Плата живиться штатно (micro-USB 5V + батарея). Pico живлення НЕ дає,
     лише спільна земля + логіка 3.3В.
```

## 2. Розводка Pico

**SWD** (як у [`../tools/swd/README.md`](../tools/swd/README.md)):
| Pico | → | GD32 |
|---|---|---|
| GP2 (SWCLK) | → | `TP16` (PA14) |
| GP3 (SWDIO) | → | `TP17` (PA13) |
| GND | → | `GND` |

**UART-міст** (Pico вдає ESP на GD32 USART1 = PA2/PA3). Паяємо до **каштеляцій ESP**:
| Pico | напрям | Пад ESP (рекоменд.) | = вузол | Роль |
|---|---|---|---|---|
| GP4 (UART **TX**, фіз. pin 6) | ──► | `GPIO17` (пад 28) | GD32 `PA3` / USART1 RX (нога 13) | Pico **шле** команди в GD32 |
| GP5 (UART **RX**, фіз. pin 7) | ◄── | `GPIO16` (пад 27) | GD32 `PA2` / USART1 TX (нога 12) | Pico **читає** вивід GD32 |
| GND (фіз. pin 3) | — | GND | — | спільна земля |

> **Увага — «не перехрещено» на боці ESP, і це правильно:** Pico заміняє ESP, тож
> під'єднується як ESP → Pico **TX→GPIO17** (ESP-TX-пад), Pico **RX→GPIO16** (ESP-RX-пад).
> Фактичне TX↔RX-перехрестя реалізовано на боці GD32 (PA3=RX, PA2=TX).
> ESP на ESPHome лишає GPIO16/17 входами (high-Z), тож конфлікту немає; альтернативно
> можна паяти до самих ніг GD32 (`PA2`/`PA3`) — електрично той самий вузол.
> (Пади: GPIO16=27, GPIO17=28 за Mischianti; у `tools/swd/README` «Pin 25» для GPIO16 — помилка, правильно 27.)

> Стокова прошивка **debugprobe** дає рівно SWD + цей двонапрямний UART-міст. Міст з'являється
> як окремий USB-CDC порт (напр. `/dev/ttyACM1`); швидкість задає хост при відкритті (наші
> SRAM-тести — 115200, як `tools/swd/uart_test.c`).

## 3. Крок ESP (робить користувач, одноразово)

1. Прошити ESP32 мінімальним ESPHome (звільняє UART + дає бездротовий доступ):
   ```bash
   pip install esphome            # або в наявний venv
   cp esphome/secrets.yaml.example esphome/secrets.yaml   # заповнити
   # first flash — по USB-UART (ТЗ §3.1: TXD0/RXD0 + GPIO0→GND), далі OTA
   esphome run esphome/htram.yaml
   ```
2. Переконатися, що ESP у мережі: `esphome logs esphome/htram.yaml`.

## 4. Що робить Claude з Bash (коли Pico на цій машині)

**SWD/OpenOCD** (перевірка зв'язку, halt, читання):
```bash
openocd -f interface/cmsis-dap.cfg -c "transport select swd" \
        -c "adapter speed 1000" -f target/stm32f1x.cfg \
        -c "init" -c "reset halt" -c "mdw 0x08000000 4" -c "exit"
```

**SRAM-проба периферії** (без прошивання flash — патерн з `tools/swd/`):
- пишемо крихітну програму (база — `tools/swd/uart_test.c` + `sram.ld`),
- `arm-none-eabi-gcc` → bin, `load_image ... 0x20000000`, set SP/PC, `resume`,
- читаємо результат по UART-мосту (`pyserial`).

**UART-команди** на GD32 (роль ESP): `pyserial` на CDC-порт мосту — слати кадри
нашого протоколу (ТЗ §5), читати телеметрію/відповіді.

Register-мапи GD32F150 (звірені, `tools/swd/uart_test.c`): `BSRR=+0x18` (set),
`BRR=+0x28` (clear), USART `BAUD=+0x0C`, `STAT=+0x1C`, `TDATA=+0x28`.

## 5. План перших проб (щойно стенд зібрано)

- [x] PyOCD: зв'язок з GD32, SRAM loader на `0x20000000`, читання по UART на `PA2/PA3` @ 115200.
- [x] SRAM-проба **LED**: `PC14` (2×G), `PB4` (1×Y), `PB5` (2×R) + живлення `PA1=1`.
- [x] Знайти **кнопку**: `PA0` (Active-HIGH, підтяжка на платі до GND).
- [x] Знайти **зумер**: `PB0` (TIMER2_CH2, 2304 Hz).
- [x] Знайти **підсвітку**: `PB8` (TIMER15_CH0 / HIGH = ON).
- [x] SRAM-проба **SHT30**: бітбенг I2C `PB6`(SCL)/`PB7`(SDA), `PA8`(nRESET), `PB2`(PWR) → T/H зчитано стабільно!
- [x] SRAM-проба **CRIR M1**: USART0 (`PA9`/`PA10`) @ 9600, `PB9`/`PB11` (5V Boost / PWR Switch), Modbus Cmd #0 `FE 04 00 07 00 01 94 04` → зчитано CO₂ (533..704 ppm)!
- [x] SRAM-проба **дисплея**: ST7789 3-wire 9-bit SPI (`PB12` RES, `PB13` SCK, `PB14` CS, `PB15` SDA, `PB8` Backlight) → тестові патерни відмальовано успішно!

