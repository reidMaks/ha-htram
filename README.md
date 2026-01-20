# Honeywell Transmission Risk Air Monitor (HTRAM) Integration

> [!WARNING]
> **Work In Progress**: This integration is currently in active development.
> **Auto-Discovery does NOT work at this time.**
> You MUST pair the device with your Home Assistant host MANUALLY (e.g., using `bluetoothctl` in the console) BEFORE adding this integration.
>
> *This integration was entirely reverse-engineered and written by **Antigravity (Google Deepmind)** with the help of a human supervisor.*

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Custom component for Home Assistant to integrate the **Honeywell Transmission Risk Air Monitor (HTRAM)** via **Bluetooth Low Energy (BLE)**.

This integration was reverse-engineered from the official (now defunct) Android application to provide full local control without cloud dependency.

## Features

*   **Real-time Monitoring**:
    *   CO2 Levels (ppm)
    *   Temperature (°C/°F)
    *   Humidity (%)
    *   Battery Level (%) & Charging Status
*   **Device Control**:
    *   **Mute Alarm**: Toggle the buzzer sound on/off.
    *   **Screen Settings**: Set auto-off timer ("Always On" vs "Auto Off (2 min)").
    *   **Alarm Thresholds**: Customize Low (Green/Yellow) and High (Yellow/Red) CO2 thresholds.
    *   **Temperature Unit**: Switch between Celsius and Fahrenheit.
    *   **Time Sync**: Synchronize device time with Home Assistant (UTC).

## Installation

### Option 1: HACS (Recommended)

1.  Open HACS in Home Assistant.
2.  Go to **Integrations** > **Custom repositories**.
3.  Add the URL of this repository.
4.  Category: **Integration**.
5.  Click **Add** and then **Download**.
6.  Restart Home Assistant.

### Option 2: Manual

1.  Download the `custom_components/htram` folder.
2.  Copy it to your Home Assistant's `config/custom_components/` directory.
3.  Restart Home Assistant.

## Configuration

### Step 1: Manual Pairing (Required)

Due to limitations in the current auto-discovery logic, you must pair the device with your OS first.

**Using SSH / Terminal:**
1.  Open your terminal.
2.  Run `bluetoothctl`.
3.  Put your HTRAM device in **Pairing Mode** (double-press button, Bluetooth icon flashes).
4.  Run `scan on`.
5.  Wait for your device to appear (look for `HTRAM-...`).
6.  Run `pair XX:XX:XX:XX:XX:XX` (replace with MAC address).
    *   *Note*: If `pair` fails, try running `connect XX:XX:XX:XX:XX:XX` instead.
7.  If a PIN appears on the device, enter it. If not, it may pair automatically ("Just Works" mode).
8.  Once paired/connected, type `exit`.

### Step 2: Add Integration

1.  Go to **Settings** > **Devices & Services**.
2.  Click **Add Integration**.
3.  Search for **HTRAM**.
4.  Select your paired device from the list.

## Usage

Once added, a new Device will be created with the following entities:

*   **Sensors**: `sensor.htram_co2`, `sensor.htram_temperature`, etc.
*   **Switch**: `switch.htram_mute` (Turn **ON** to mute the device).
*   **Selects**:
    *   `select.htram_screen_off_timer`: Choose "Always On" or "Auto Off (2 min)".
    *   `select.htram_temperature_unit`: Celsius / Fahrenheit.
*   **Numbers**:
    *   `number.htram_co2_alarm_low`: Threshold for yellow warning.
    *   `number.htram_co2_alarm_high`: Threshold for red alarm.
    *   `number.htram_co2_alarm_low`: Threshold for yellow warning.
    *   `number.htram_co2_alarm_high`: Threshold for red alarm.
*   **Select**: `select.htram_temperature_unit`.
*   **Button**: `button.htram_sync_time`.

## Troubleshooting

*   **Bluetooth Range**: Ensure the device is close to your Home Assistant host or a Bluetooth Proxy.
*   **Polling**: Data is updated every 60 seconds to save battery.
*   **Battery Level**: The device reports battery in "bars" (0-4). The integration estimates this as 0%, 25%, 50%, 75%, 100%.

## Disclaimer

This is an unofficial integration and is not affiliated with Honeywell. Use at your own risk.
