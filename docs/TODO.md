# HTRAM — TODO / залишок робіт

Статус на 2026-09-05. Пристрій піднімається з холодного старту: дисплей, ESP,
сенсори, батарея — усе працює (див. [GD32_HARDWARE_MAP §6.9](GD32_HARDWARE_MAP.md)).
Наша GD32-прошивка в `main` (не пушено). ESP уже на **власному ESPHome-компоненті**
`htram_gd32`: читає телеметрію GD32 і шле downlink на 115200 (§3), провіжиниться
через штатний `wifi`/`api`/`web_server` (§9). Лишається міграція на 921600 (за §1),
`DRAW_RECT` (§4) і решта функціоналу.

Легенда пріоритетів: 🔴 блокер / фундамент · 🟡 функціонал · 🟢 доробки.

Загальний інструментарій: заливка GD32 — [`tools/swd/flash.py`](../tools/swd/flash.py)
(`.venv/bin/python tools/swd/flash.py`), SRAM-проби й діагностика —
[`tools/bench.py`](../tools/bench.py), відкат до заводської — `flash.py --factory`,
доки заліза — [GD32_HARDWARE_MAP.md](GD32_HARDWARE_MAP.md), специфікація —
[CUSTOM_FIRMWARE_SPEC.md](CUSTOM_FIRMWARE_SPEC.md), стендові процедури —
[BENCH.md](BENCH.md).

---

## 1. 🔴 PLL 72 МГц + рекалібрування таймінгів

**Що:** чіп працює на IRC8M 8 МГц; закладено 72 МГц. Усі софт-затримки
([`delay_us`/`delay_ms`/`delay_cycles`](../firmware/gd32/inc/gd32f150.h)) і все
бітбенг-тактування (зумер, дисплей, I2C) відкалібровані під 8 МГц. Треба підняти
PLL до 72 МГц і перерахувати `SYSTEM_CLOCK_HZ` + усі затримки.

**Чому фундамент:** блокує (а) коректну частоту зумера (зараз чути лише один біп —
друга частота вилітає за діапазон п'єзо), (б) UART 921600 бод до ESP, без якого
`DRAW_RECT`/піксельний канал нежиттєздатні (§2, §4).

**Очікуваний результат:** `RCU` налаштований на PLL×9 від IRC8M/2 (або HXTAL,
якщо розведено) = 72 МГц; `SYSTEM_CLOCK_HZ=72000000`; `USART1_BAUD` перерахований;
зумер видає задані частоти (перевірити 1000/2500 Гц на слух), дисплей і SHT30
читаються стабільно на новій швидкості; телеметрія й приймання не деградували.

**Файли/доки:** [`firmware/gd32/inc/gd32f150.h`](../firmware/gd32/inc/gd32f150.h)
(RCU_CTL/CFG0, delay_*), [`firmware/gd32/src/periph.c`](../firmware/gd32/src/periph.c)
(`periph_beep`), [`firmware/gd32/src/display.c`](../firmware/gd32/src/display.c),
[`firmware/gd32/src/sensors.c`](../firmware/gd32/src/sensors.c),
`GD32F150xx Datasheet Rev4.0.pdf` (розділ RCU). **Інструмент:** `flash.py`,
`tools/bench.py` для стендової перевірки таймінгів SRAM-пробою.

**Ризик:** зміна торкається кожного модуля — після PLL перевірити геть усе.
Робити на підключеному живленні + Pico (реальна швидкість != під відладчиком у SRAM).

---

## 2. 🔴 Power-off / standby як стан прошивки

**Що:** реалізувати правильне «вимкнення» — GD32 лишається живим (він на
always-on домені), гасить периферію (`PF7`/`PB3` low, дисплей у режим індикатора
батареї, LED off, сенсори/буст off), і стежить за кнопкою для повторного
ввімкнення. **Не** обрив DC-DC (спроба з `PC15` відкочена — див. коміт `4dcb14c`).

**Чому:** заводська так і робить (індикатор батареї при «вимкненому»); наша
прошивка зараз стартує одразу в «on». Це те, чого чекає користувач від кнопки.

**Очікуваний результат:** утримання кнопки ~3с у стані «on» → перехід у standby
(екран показує лише великий індикатор батареї/заряду, ESP/сенсори знеструмлені,
`PF7`=low); утримання у standby → повне ввімкнення. Пристрій не зависає, SWD
лишається живим. Звірити послідовність зі станом заводської (читати живі GPIO
standby через SWD — метод §6.9).

**Файли/доки:** [`firmware/gd32/src/main.c`](../firmware/gd32/src/main.c)
(цикл+кнопка), [`firmware/gd32/src/periph.c`](../firmware/gd32/src/periph.c),
[GD32_HARDWARE_MAP §6.6/§6.9](GD32_HARDWARE_MAP.md),
[CUSTOM_FIRMWARE_SPEC §9.8](CUSTOM_FIRMWARE_SPEC.md). **Інструмент:** `flash.py`,
`flash.py --factory` для зчитування еталонної standby-послідовності.

---

## 3. 🟢 UART-протокол на боці ESPHome (GD32 ↔ ESP) — базово ВИКОНАНО @ 115200

**Статус:** реалізовано власний компонент
[`esphome/custom_components/htram_gd32/`](../esphome/custom_components/htram_gd32/):
парсить телеметрію (`0xAA55`, тип 0x01, CRC-16-CCITT) у сутності HA і шле downlink
(BEEP/SET_LEDS/BACKLIGHT/ENTER_BOOTLOADER), підключений у
[`htram.yaml`](../esphome/htram.yaml) (`uart` tx=GPIO17/rx=GPIO16 @ 115200).
**Лишилось:** міграція на 921600 (потребує §1) і downlink `DRAW_RECT` (§4).

**Що було:** стоковий ESPHome нічого не слав у GD32 (тому `rx_head=0` — норма). Додати
в конфіг ESP компонент/`uart`, що: (а) парсить нашу телеметрію (`0xAA55`, тип
0x01, CRC-16-CCITT), (б) шле downlink-команди (SET_LEDS/BACKLIGHT/BEEP/DRAW_RECT).

**Очікуваний результат:** ESP на `PA2↔GPIO16`, `PA3↔GPIO17` @ 115200 (згодом
921600) читає телеметрію GD32 у сутності HA (CO2/T/H/батарея/статус) і успішно
надсилає команди (перевірка: BEEP/SET_LEDS змінюють стан пристрою; телеметрія
з'являється в HA). `rx_head` на GD32 починає рости.

**Файли/доки:** [`esphome/htram.yaml`](../esphome/htram.yaml),
[`custom_components/htram/protocol.py`](../custom_components/htram/protocol.py)
(еталон CRC/формату), [`firmware/gd32/src/protocol_engine.c`](../firmware/gd32/src/protocol_engine.c)
та [`firmware/gd32/inc/protocol.h`](../firmware/gd32/inc/protocol.h) (формат кадрів),
[CUSTOM_FIRMWARE_SPEC §5](CUSTOM_FIRMWARE_SPEC.md). **Інструмент:** для локальної
перевірки протоколу без ESP — [`tools/htram_uart.py`](../tools/htram_uart.py),
[`tools/decode_telemetry.py`](../tools/decode_telemetry.py) через Pico-міст.

---

## 4. 🟡 Дисплей: канал `DRAW_RECT` / UI від ESP

**Що:** зараз GD32 малює власний текстовий UI (CO2/TEMP/HUM/BATT) сам. За спекою
UI (Варіант A / LVGL) рендериться на ESP і стрімиться в GD32 як піксельні
прямокутники (`CMD_TYPE_DRAW_RECT=0x10`). Реалізувати генерацію на ESP і перевірити
приймач у [`protocol_engine.c`](../firmware/gd32/src/protocol_engine.c) (стан
`STATE_PIXELS`).

**Очікуваний результат:** ESP надсилає кадр/регіон, GD32 виводить його на ST7789
без артефактів; стабільно на 921600 (потребує §1).

**Файли/доки:** [CUSTOM_FIRMWARE_SPEC §4.2/§9](CUSTOM_FIRMWARE_SPEC.md),
[`firmware/gd32/src/display.c`](../firmware/gd32/src/display.c),
[`esphome/htram.yaml`](../esphome/htram.yaml). **Залежить від:** §1, §3.

---

## 5. ✅ OTA GD32 через ESP (Резидентний завантажувач у SRAM) — ВИКОНАНО

**Що зроблено:**
- Виявлено апаратний баг у заводському ROM-завантажувачі GigaDevice (`0x1FFFEC00`): на `PA3` він замість USART1 ініціалізує регістри USART0 і шле ACK `0x79` на `PA9` (сенсор CO₂).
- Створено **власний резидентний флешер у SRAM** ([`firmware/gd32/src/flasher.c`](../firmware/gd32/src/flasher.c)), розміщений у секції `.ramcode` у внутрішній пам'яті.
- GD32 обробляє команду `CMD_ENTER_BOOTLOADER` (`0x1F`, ключ `0xDEADBEEF`), надсилає підтвердження `0xAA 0x55 0x1F 0x79` і викликає `flasher_run()`.
- Резидентний флешер працює на USART1 (115200 8N1), стирає Flash, прошиває 256-байтними чанками і по команді `CMD_GO` (0x21) виконує плавний перехід (soft-jump) на вектор `Reset_Handler` без скидання живлення периферії (PF7 залишається HIGH, ESP32 не перезавантажується).
- ESPHome ендпоінт `/gd32_ota` та CLI `tools/swd/flash.py --ota htram.local` протестовані на живому залізі: 7760 байт прошиваються за **889 мс** (8.7 КБ/с), після чого телеметрія продовжує надходити автоматично.
- `flash.py` звіряє `staged_crc` (від ESP) з локальним `host_crc` — наскрізний контроль цілісності завантаження.

**Ризик:** mass-erase стирає весь Flash, зокрема й завантажувальний образ
резидентного флешера. Якщо між стиранням і успішним `CMD_GO` пропаде живлення /
ребутнеться ESP — GD32 лишиться без прошивки й без флешера → відновлення лише по
SWD. Пом'якшено батарейним гейтом `>3500 mV`; у межах однієї сесії живлення ретрай
OTA працює (флешер живе в SRAM).

**Файли:** [`firmware/gd32/src/flasher.c`](../firmware/gd32/src/flasher.c),
[`firmware/gd32/inc/flasher.h`](../firmware/gd32/inc/flasher.h),
[`esphome/custom_components/htram_gd32/htram_gd32.cpp`](../esphome/custom_components/htram_gd32/htram_gd32.cpp),
[`tools/swd/flash.py`](../tools/swd/flash.py).

---

## 6. 🟡 Логіка кнопки (short/long/very-long)

**Що:** за [§9.8](CUSTOM_FIRMWARE_SPEC.md) — коротке: перемикання екранів; довге
(~2с): підсвітка; дуже довге (~10с): скид Wi-Fi provisioning. Прикладна логіка —
на ESP (GD32 віддає біт `STATUS_FLAG_BUTTON_PRESSED`), окрім power on/off (§2,
живе в GD32). Розмежувати тривалості між GD32 (power) і ESP (app).

**Очікуваний результат:** ESP отримує події кнопки й виконує прикладні дії; power
on/off лишається в GD32 і не конфліктує з app-логікою.

**Файли/доки:** [`firmware/gd32/src/main.c`](../firmware/gd32/src/main.c),
[`esphome/htram.yaml`](../esphome/htram.yaml), [§9.8](CUSTOM_FIRMWARE_SPEC.md).

---

## 7. 🟢 Узгодити порядок полів `CMD_SET_LEDS`

**Що:** код приймає `R,Y,G,Brightness`
([`protocol_engine.c`](../firmware/gd32/src/protocol_engine.c) `STATE_TYPE`), а
спека §5.2 описує Green/Yellow/Red. Звірити з
[`custom_components/htram/protocol.py`](../custom_components/htram/protocol.py) і
привести до одного порядку по всьому стеку (GD32 ↔ ESP ↔ HA).

**Очікуваний результат:** команда «зелений» вмикає зелений на всіх рівнях;
задокументовано в §5.2.

---

## 8. 🟢 Верифікувати біти статусу батареї/заряду

**Що:** підтвердити семантику `STATUS_FLAG_USB_PRESENT`/`CHARGING` (PC13/PA15) на
живому пристрої в різних станах (акум / USB / заряджання), і що формула батареї
`mV=(raw*3275)>>11` тримається під навантаженням периферії (аномалія 494мВ була
симптомом нестабільної рейки, §6.9 — переконатись, що не повертається).

**Очікуваний результат:** статус-біти й напруга збігаються з реальним станом
(звірка з референсним пристроєм по MQTT, як у §6.7).

**Файли/доки:** [`firmware/gd32/src/periph.c`](../firmware/gd32/src/periph.c)
(`periph_read_battery`), [GD32_HARDWARE_MAP §6.7](GD32_HARDWARE_MAP.md).
**Інструмент:** `tools/swd/battery_test.c` (SRAM-проба).

---

## 9. ✅ ESPHome: Wi-Fi provisioning + native API у HA — ВИКОНАНО

**Статус:** [`htram.yaml`](../esphome/htram.yaml) містить `wifi:` (+ `captive_portal`),
`api:`, `web_server:` та `ota:`. Пристрій провіжиниться штатним потоком і
з'являється в HA через native API. Лишилось лише навісити решту сутностей у міру
готовності відповідного функціоналу (кнопка §6 тощо).

**Що:** провіжинг і HA-інтеграцію повністю закриває **сам ESPHome** — це частина
конфігу §3, не окрема робота: `wifi:` (+ improv/captive-portal за потреби) і
`api:` (native API, HA автовиявляє пристрій з усіма сутностями). Окремий
UART-протокол до GD32 — у §3.

**Очікуваний результат:** пристрій провіжиниться штатним ESPHome-потоком і
з'являється в HA через native API з усіма сутностями (CO2/T/H/батарея/статус +
кнопки/налаштування); окремий MQTT не потрібен.

**Файли/доки:** [`esphome/htram.yaml`](../esphome/htram.yaml),
[CUSTOM_FIRMWARE_SPEC §9.1](CUSTOM_FIRMWARE_SPEC.md).

> **Legacy (не для ESPHome-шляху):** [`custom_components/htram/`](../custom_components/htram/)
> (mqtt_source/config_flow/coordinator) і відновлений MQTT downlink-формат
> `D/<serial>` — це шлях керування **заводською** прошивкою ESP через її
> MQTT/cloud-протокол. На ESPHome-шляху **не потрібні** (native API замінює
> MQTT). Свою роль (ціль реверсу — дамп прошивки) вони вже відіграли. Рішення:
> прибрати/архівувати, або лишити тільки якщо хочемо підтримувати ще й непрошиті
> заводські пристрої. Референс формату/CRC: `custom_components/htram/protocol.py`.

---

### Рекомендований порядок
Виконано: `§5 (OTA)`, `§9 (wifi/api)`, `§3` (базово @ 115200). Далі:
`§1 (PLL)` → підняти §3-лінк на 921600 → `§2 (standby)` паралельно →
далі `§4 (display)`, `§6 (button)` → доробки `§7, §8`. §1 лишається
фундаментом для 921600 і §4. MQTT/окрема HA-інтеграція **вилучені** з
плану (ESPHome native API їх замінює).
