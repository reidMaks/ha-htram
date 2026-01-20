# Honeywell Transmission Risk Air Monitor (HTRAM) Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Custom component for Home Assistant to integrate the **Honeywell Transmission Risk Air Monitor (HTRAM-RM)** via **Bluetooth Low Energy (BLE)**.

This integration was reverse-engineered from the official (now defunct) Android application to provide full local control without cloud dependency.

## Features

*   **Real-time Monitoring**:
    *   CO2 Levels (ppm)
    *   Temperature (°C/°F)
    *   Humidity (%)
    *   Battery Level (%) & Charging Status
*   **Device Control**:
    *   **Mute Alarm**: Toggle the buzzer sound on/off.
    *   **Screen Settings**: Set auto-off timer (Always On or custom minutes).
    *   **Alarm Thresholds**: Customize Low (Green/Yellow) and High (Yellow/Red) CO2 thresholds.
    *   **Temperature Unit**: Switch between Celsius and Fahrenheit.
    *   **Time Sync**: Synchronize device time with Home Assistant (UTC).
*   **Auto-Discovery**: Automatically finds devices in pairing mode.

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

### Auto-Discovery (Recommended)

1.  Put your HTRAM device in **Pairing Mode** (usually double-press the main button until the Bluetooth icon flashes).
2.  Go to **Settings** > **Devices & Services**.
3.  You should see "HTRAM" discovered. Click **Configure**.

### Manual Add

1.  Go to **Settings** > **Devices & Services**.
2.  Click **Add Integration**.
3.  Search for **HTRAM**.
4.  Select your device from the list.

## Usage

Once added, a new Device will be created with the following entities:

*   **Sensors**: `sensor.htram_co2`, `sensor.htram_temperature`, etc.
*   **Switch**: `switch.htram_mute` (Turn **ON** to mute the device).
*   **Numbers**:
    *   `number.htram_screen_off_timer`: Set to 0 for "Always On", or 120 for 2 minutes.
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
