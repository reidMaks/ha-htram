# HTRAM — TODO / залишок робіт

Статус на 2026-09-05. Пристрій піднімається з холодного старту: дисплей, ESP,
сенсори, батарея — усе працює (див. [GD32_HARDWARE_MAP §6.9](GD32_HARDWARE_MAP.md)).
ESP на **власному ESPHome-компоненті** `htram_gd32`: телеметрія + downlink @ 115200,
`wifi`/`api`/`web_server`. Виконано (див. §5, §6, §7, §9, §10): OTA GD32, build-id GD32 в HA,
PWM-димінг підсвітки (TIMER15), синхронізація стану LED GD32→HA + % заряду + USB/charging,
не-блокуючий бузер/мелодії (TIMER2) з **RTTTL-службами HA**, кнопка з апаратним SysTick-таймінгом,
миттєвим uplink `EVT_BUTTON` та **мульти-кліками в HA** (`single`/`double`/`triple`/`many`/`long`).
Комітиться в `main` (не пушено).

**Наступні кроки — див. розділ у кінці.** Коротко: standby (§2) →
дисплей/`DRAW_RECT` (§4, найбільший, потребує DMA-фундаменту) → доробка §8 / авто-LED. Про 921600 —
чіп **уже на 72 МГц** (§1 знято), і baud не блокер; вузьке місце для стріму — RX-конвеєр GD32 (§4).

Легенда пріоритетів: 🔴 блокер / фундамент · 🟡 функціонал · 🟢 доробки.

Загальний інструментарій: заливка GD32 — [`tools/swd/flash.py`](../tools/swd/flash.py)
(`.venv/bin/python tools/swd/flash.py`), SRAM-проби й діагностика —
[`tools/bench.py`](../tools/bench.py), відкат до заводської — `flash.py --factory`,
доки заліза — [GD32_HARDWARE_MAP.md](GD32_HARDWARE_MAP.md), специфікація —
[CUSTOM_FIRMWARE_SPEC.md](CUSTOM_FIRMWARE_SPEC.md), стендові процедури —
[BENCH.md](BENCH.md).

---

## 1. ✅ PLL 72 МГц — ЗНЯТО (premise був хибний)

Виявилось, що [`system_clock_config()`](../firmware/gd32/src/periph.c) **вже** піднімає
PLL з IRC8M/2 ×18 = 72 МГц і виставляє flash latency 2WS; `SYSTEM_CLOCK_HZ=72000000`,
усі `delay_*` рахуються від 72 МГц, і пристрій стабільно працює (дисплей, SHT30, UART,
телеметрія). Тобто окремого «підняти PLL» робити не треба.

Частота бузера, що згадувалась тут як блокер, **вирішена окремо**: `periph_beep`/мелодії
тепер на апаратному PWM (`PB0 = TIMER2_CH2`), частота задається точно (не бітбенг).
Підсвітка — PWM на `PB8 = TIMER15_CH0`.

> Єдине, що лишилось із «клокової» теми — **джерело**: PLL береться з внутрішнього
> **IRC8M** (не кварц), ~±1% з дрейфом по температурі. Для телеметрії/команд байдуже;
> релевантно лише як загальний стель надійності швидкого serial (див. §4).

---

## 2. ✅ Power-off / standby як стан прошивки — ВИКОНАНО

**Що зроблено:**
- Реалізовано вхід у Standby по утриманню кнопки ~3 с у [`main.c`](../firmware/gd32/src/main.c):
  - Бузер видає підтверджувальний біп `periph_beep_blocking(2000, 100)`.
  - Дисплей очищається і підсвітка гаситься (`display_set_backlight(0)`).
  - Знеструмлюються сенсори (`GPIOB_BC = (1 << 11) | (1 << 9)`).
  - Гасяться індикаторні світлодіоди та вимикається їхня рейка `VLED` (`GPIOA_BC = (1 << 1)`).
  - Повністю знеструмлюється ESP32 (`PF7 = LOW`, `PB3 = LOW`).
- Очікування відпускання кнопки та перехід у мікро-цикл опитування кнопки SW1.
- Пробудження: утримання кнопки ~2 с виконує чистий старт системи (`SYSRESETREQ`), піднімаючи живлення периферії, сенсори та ESP32 з відомого стабільного стану.

**Файли:** [`firmware/gd32/src/main.c`](../firmware/gd32/src/main.c).

---

## 3. 🟢 UART-протокол на боці ESPHome (GD32 ↔ ESP) — базово ВИКОНАНО @ 115200

**Статус:** реалізовано власний компонент
[`esphome/custom_components/htram_gd32/`](../esphome/custom_components/htram_gd32/):
парсить телеметрію (`0xAA55`, тип 0x01, CRC-16-CCITT) у сутності HA і шле downlink
(BEEP/SET_LEDS/BACKLIGHT/PLAY_MELODY/ENTER_BOOTLOADER), підключений у
[`htram.yaml`](../esphome/htram.yaml) (`uart` tx=GPIO17/rx=GPIO16 @ 115200).
**Лишилось:** downlink `DRAW_RECT` (§4). Міграція на 921600 baud **не потрібна** для
телеметрії/команд (115200 з запасом); релевантна лише для піксельного стріму §4, де
справжнє вузьке місце — не baud, а RX-конвеєр (див. §4), тож 921600 йде в комплекті з §4.

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
без артефактів.

**Вузьке місце — НЕ baud (аналіз прошивки).** 921600 сам по собі досяжний уже зараз:
USART1 fCK = APB1 = 36 МГц, дільник `36e6/921600 = 39` (+0.16%), HW-стеля ~2.25 Мбод.
Проблема — **RX-конвеєр GD32 під безперервним стрімом**:
- кільце RX лише **256 Б**, дренаж — блокуючим main-loop; будь-який блок (bitbang-рефреш
  дисплея, CO2-таймаут) стопить дренаж. Запас: 22 мс @115200, але лише **2.78 мс @921600** →
  на щільному пікселстрімі кільце переповнюється;
- **переривання на кожен байт** (кожні ~10.85 мкс @921600);
- **дисплей — бітбенг SPI** (PB13/PB15), що саме по собі впирається в CPU незалежно від UART.

Тому для §4 потрібні фундаменти в прошивці GD32:
1. **USART1 RX на DMA** у велике кільце (знімає per-byte ISR і розв'язує з блокуванням циклу);
2. **дисплей на апаратному SPI + DMA** замість бітбенгу;
3. не-блокуючий main-loop (бузер уже такий).

**Файли/доки:** [CUSTOM_FIRMWARE_SPEC §4.2/§5.2](CUSTOM_FIRMWARE_SPEC.md),
[`firmware/gd32/src/display.c`](../firmware/gd32/src/display.c),
[`firmware/gd32/src/protocol_engine.c`](../firmware/gd32/src/protocol_engine.c) (`STATE_PIXELS`),
[`esphome/htram.yaml`](../esphome/htram.yaml). **Залежить від:** §3 (не від §1 — клок уже 72 МГц).

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

## 6. ✅ Логіка кнопки (instant uplink + мульти-кліки в HA) — ВИКОНАНО

**Що зроблено:**
- **Антидрязгіт та таймінг на GD32:** програмний фільтр (~15 мс) у [`main.c`](../firmware/gd32/src/main.c);
  локальний SysTick-підрахунок точного часу утримання кнопки в мілісекундах.
- **Протокол:** додано пакет `PKT_TYPE_BUTTON = 0x03` (`magic(2) + type(1) + state(1) + duration_ms(2) + crc16(2)` = 8 байтів).
  GD32 шле миттєвий uplink: `state=1, dur=0` одразу при натисканні (з коротким зворотним «піком» бузера 20 мс)
  та `state=0, dur=N` у момент відпускання.
- **Збереження автономного живлення на GD32:** утримання 3 с переводить GD32 у Standby (вимикає живлення ESP `PF7`),
  утримання 2 с у Standby виконує скидання та пробудження. ESP у логіку живлення не втручається.
- **ESPHome інтеграція:**
  - `binary_sensor.button` («Button») — миттєвий статус натискання (ON під час утримання, OFF при відпусканні).
  - `text_sensor.button_action` («Button Action») — розпізнавання жестів:
    - 1 клік → `single`
    - 2 кліки → `double`
    - 3 кліки → `triple`
    - 4 кліки → `quadruple`
    - 5+ кліків → `many`
    - Утримання (0.6 – 2.8 с) → `long`
  - Автоскидання через 1 с у `""` для надійного повторного спрацьовування тригерів автоматизацій у Home Assistant.

**Файли:** [`firmware/gd32/src/main.c`](../firmware/gd32/src/main.c),
[`firmware/gd32/inc/protocol.h`](../firmware/gd32/inc/protocol.h),
[`firmware/gd32/src/protocol_engine.c`](../firmware/gd32/src/protocol_engine.c),
[`esphome/custom_components/htram_gd32/`](../esphome/custom_components/htram_gd32/) (`binary_sensor.py`, `text_sensor.py`, `htram_gd32.cpp`, `htram_gd32.h`).

---

## 7. ✅ Узгодити порядок полів `CMD_SET_LEDS` — ВИКОНАНО

Порядок `R,Y,G` узгоджено по всьому стеку (GD32 [`protocol_engine.c`](../firmware/gd32/src/protocol_engine.c) ↔
ESP `send_leds`/switch-платформа ↔ HA), спека §5.2 оновлена. Байт `Brightness`
**ігнорується** — залізо не димить індикаторні LED (спільна рейка VLED з конденсатором);
слайдер «LED Brightness» прибрано, LED керуються on/off, а їхній стан ще й
синхронізується назад у HA бітами 5–7 статусу телеметрії.

---

## 8. ✅ Верифікація бітів статусу батареї/заряду — ВИКОНАНО

**Що перевірено на живому залізі:**
- `PC13` (`USB_PRESENT`): `1` (`ON`) при підключенні живлення 5V, `0` (`OFF`) при витяганні кабелю.
- `PA15` (`CHARGING`): `1` (`ON`) під час заряджання від USB, `0` (`OFF`) під час роботи від акумулятора.
- Вимірювання напруги (`PB1 / ADC_IN9`, формула `(raw * 3275) >> 11`):
  - При роботі суто від батареї: напруга стабільна (~3530 мВ під навантаженням плати ~300 мА).
  - При підключенні USB: напруга зростає до 3575–3583 мВ, показуючи наявність зарядного струму.
  - Розрахунок відсотків (`batt_mv_to_pct`): коректно відображає 18% (без USB) та 24–25% (під час заряду).
- Безшовний перехід: плата без збоїв та brown-out перемикається між USB та акумулятором.

**Файли:** [`firmware/gd32/src/periph.c`](../firmware/gd32/src/periph.c),
[`esphome/custom_components/htram_gd32/htram_gd32.cpp`](../esphome/custom_components/htram_gd32/htram_gd32.cpp).

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

## 10. ✅ Бузер: тон/мелодії + build-id/діагностика — ВИКОНАНО

- **Не-блокуючий бузер** на `PB0 = TIMER2_CH2` PWM (був бітбенг у циклі, що блокував RX).
  `periph_beep` тепер ставить ноту в чергу; `periph_beep_blocking` — для старт/standby.
- **Мелодії** — downlink `CMD_PLAY_MELODY` (`0x14`), стрім `count×(freq16,dur16)` до 96 нот,
  програвання по системному SysTick 1ms ([`periph.c`](../firmware/gd32/src/periph.c) `periph_play_melody`/`periph_buzzer_tick`).
  Імпульс ШІМ обмежено до 400 мкс для усунення магнітного насичення котушки й хрипу на низьких нотах.
- **RTTTL з HA:** ESP парсить RTTTL-рядок → ноти → стрім у GD32. Служби HA
  `esphome.htram_play_rtttl(song)` та `stop_melody`.
- **Build-id GD32 в HA:** HELLO розширено (`build_epoch`+`git_hash`+dirty), Makefile інжектить
  на кожному білді; сутність «GD32 Firmware» показує `X.Y.Z g<hash>[+] (дата UTC)`.

**Файли:** [`firmware/gd32/src/periph.c`](../firmware/gd32/src/periph.c),
[`firmware/gd32/src/protocol_engine.c`](../firmware/gd32/src/protocol_engine.c) (`STATE_MELODY`),
[`esphome/custom_components/htram_gd32/htram_gd32.cpp`](../esphome/custom_components/htram_gd32/htram_gd32.cpp)
(`play_rtttl`), [`htram.yaml`](../esphome/htram.yaml) (services).

---

## Наступні кроки (з контекстом)

Порядок за зростанням ризику/обсягу. §6 і §2 незалежні від §4; §4 — окремий великий етап.

### Крок 1 — §6 Логіка кнопки (S/L/Multi) · ✅ ВИКОНАНО
- Реалізовано апаратний антидрязгіт (~15 мс) на GD32.
- Додано протокольний мікро-пакет `PKT_TYPE_BUTTON` (`0x03`): миттєвий uplink натискання та відпускання з точним часом утримання (`duration_ms`), порахованим по SysTick.
- Збережено 100% автономну логіку живлення на GD32: утримання кнопки 3с вимикає пристрій у Standby (гасить рейку ESP32 `PF7`), утримання 2с у Standby будить пристрій.
- В ESPHome додано `binary_sensor.button` («Button») для живого стану та `text_sensor.button_action` («Button Action») з повною класифікацією: `single`, `double`, `triple`, `quadruple`, `many` та `long`, з автоскиданням в `""` через 1 с.
- GD32 та ESPHome прошивки верифіковано та оновлено на живому пристрої.

### Крок 2 — §2 Power-off / standby · ✅ ВИКОНАНО
- Утримання 3 с вмикає глибокий Standby: гасить підсвітку/дисплей, вимикає живлення сенсорів (PB11/PB9), гасить VLED/світлодіоди та повністю знеструмлює ESP32 (PF7/PB3).
- Пробудження: утримання 2 с у Standby здійснює чистий старт системи (`SYSRESETREQ`), піднімаючи всю периферію. Працює автономно на GD32.

### Крок 3 — §4 Дисплей `DRAW_RECT` (ESP-рендер) · 🟡 великий, потребує фундаменту
Найбільший блок. Спершу — **прошивкові фундаменти на GD32** (без них стрім захлинеться, див.
аналіз у §4 вище):
1. **USART1 RX на DMA** у велике кільце (замість per-byte ISR + 256-Б кільця, що переповнюється
   за 2.78 мс блокування @921600).
2. **Дисплей на апаратному SPI + DMA** замість бітбенгу (`PB13`/`PB15`) — інакше пікселі
   впираються в CPU, а не в UART. Знайти/перевірити доступний SPI-периферійний блок під ці піни.
3. Потім — генерація UI на ESP (Варіант A: число CO₂ + графік) і стрім регіонами; приймач
   `STATE_PIXELS` у [`protocol_engine.c`](../firmware/gd32/src/protocol_engine.c) вже є (перевірити на швидкості).
4. Підняти лінк до **921600** (дільник 39, +0.16% — тривіально) саме на цьому етапі.
**Стратегічна розвилка (впливає на бюджет ESP):** Bluetooth-проксі на ESP **конфліктує** з
цим шляхом (web_server vs BT-стек; радіо/CPU vs пікселстрім) — вибрати одне з двох на
WROOM-32, або мігрувати на ESP32-S3. Рішення прийняти **до** старту §4.
**Залежить від:** §3 (готово). **Файли:** [CUSTOM_FIRMWARE_SPEC §4.2/§5.2](CUSTOM_FIRMWARE_SPEC.md),
[`display.c`](../firmware/gd32/src/display.c), [`protocol_engine.c`](../firmware/gd32/src/protocol_engine.c), [`htram.yaml`](../esphome/htram.yaml).

### Крок 4 — §8 Верифікація батареї/заряду · ✅ ВИКОНАНО
- Підтверджено роботу на живому залізі: коректні перемикання USB/Charging, точний замір напруги на акумуляторі (3530 мВ) та при заряджанні (3575–3583 мВ), безшовний перехід живлення.

### Наскрізні доробки (fast-follow)
- **Мелодії:** покрито службою `esphome.htram_play_rtttl(song)` з Home Assistant (мелодії граються напряму через RTTTL-рядки з HA).
- **Індикація за порогами CO₂** (обговорено): автоматичне підсвічування LED (G / Y / R) за рівнем CO₂ + перемикач «LED Auto» в HA. Суто ESP-логіка.
- **Архівувати legacy `custom_components/htram/`** (MQTT-шлях) — своє відіграв (див. §9),
  заодно прибрати передіснуючий збій pytest (несумісність `paho-mqtt`).
- **Push у remote** — досі все локально в `main`, не пушено.
