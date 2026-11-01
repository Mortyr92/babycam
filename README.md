# BabyCam – fully local baby monitor for Home Assistant

[![Validate](https://github.com/Mortyr92/babycam/actions/workflows/validate.yml/badge.svg)](https://github.com/Mortyr92/babycam/actions/workflows/validate.yml)

BabyCam turns a local IP camera (built and tested around the **Reolink E1 Pro**) into a baby monitor
inside Home Assistant. Everything runs on your own hardware: snapshots, AI detection, audio and video
never leave your network. There is no cloud account, no telemetry and no runtime download.

## Features

- **Sidebar panel** (`BabyCam`) with live view, status, start/stop buttons and quick toggles.
- **Camera selection** – pick any existing Home Assistant `camera.*` entity (e.g. from the official
  Reolink integration) or connect directly to a Reolink camera with host + credentials.
- **Snapshot polling** with a configurable interval (number entity, 5 – 600 s).
- **Local AI presence detection** – a lightweight YOLOv8‑nano ONNX model checks each snapshot for a
  person/baby inside a configurable *bed region* of the frame.
- **Automatic monitoring** – when a baby is detected, monitoring starts: constant audio stream to
  selected media players, cry detection, connection alarm. When the bed stays empty for *N*
  snapshots, monitoring stops again.
- **Manual monitoring** – switch, service or panel button for when the AI did not see the baby.
- **Video modes** – snapshots, low definition (sub stream) or high definition (main stream),
  selectable from the panel or a `select` entity.
- **Cry detection (optional)** – YAMNet ONNX listens to the camera audio; `binary_sensor.*_crying`
  can drive automations, and notifications are sent to selectable `notify.*` services.
- **Connection alarm** – if snapshots fail during monitoring, `binary_sensor.*_connection_alarm`
  turns on, an event fires and a notification is sent.
- Everything is exposed as entities, events and services for automations.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → *Custom repositories* → add `https://github.com/Mortyr92/babycam`
   (category *Integration*).
2. Install **BabyCam** and restart Home Assistant.

### Manual

Copy `custom_components/babycam` into `<config>/custom_components/` and restart.

`ffmpeg` must be available (it is on Home Assistant OS/Container). The Python requirements
(`onnxruntime`, `numpy`, `Pillow`) are installed automatically by Home Assistant on first start.

## Models (one‑time, offline)

BabyCam never downloads anything by itself. Put the two ONNX models into
`<config>/babycam/models/` (the folder is created on first start):

| File           | Purpose                       | Size   |
| -------------- | ----------------------------- | ------ |
| `yolov8n.onnx` | presence (person) detection   | ~13 MB |
| `yamnet.onnx`  | cry detection (optional)      | ~15 MB |

Create them on any PC with the bundled script (this is the only step that touches the internet,
and it happens on your PC, not in Home Assistant):

```bash
pip install ultralytics tensorflow tensorflow-hub tf2onnx
python scripts/export_models.py --out ./models
# copy ./models/*.onnx to <config>/babycam/models/
```

If you already have equivalent ONNX files you can use them; set the file names in the integration
options. YOLOv8 exports must use 640×640 input and the standard `[1, 84, 8400]` output; the YAMNet
export must accept a float32 16 kHz waveform and output the 521 AudioSet scores.

## Setup

1. *Settings → Devices & services → Add integration → BabyCam*.
2. Choose the camera source:
   - **Existing camera entity** – e.g. `camera.baby_room_sub` from the Reolink integration.
   - **Reolink (direct)** – IP, user, password. Defaults match the E1 Pro
     (`h264Preview_01_main` / `h264Preview_01_sub`, RTSP 554).
3. Open the integration **options** to tune:
   - snapshot interval, presence threshold, absence grace (snapshots before "not in bed"),
   - bed region of interest `x1,y1,x2,y2` as fractions of the frame (e.g. `0.2,0.1,0.8,0.9`),
   - connection timeout for the alarm,
   - cry detection on/off, threshold and notification cooldown,
   - notification services (`notify.mobile_app_*`) and media players for the audio stream,
   - model file names.
4. Open **BabyCam** in the sidebar.

## Entities

| Entity                                | Description                                                   |
| ------------------------------------- | ------------------------------------------------------------- |
| `binary_sensor.<name>_baby_in_bed`    | AI sees a baby in the bed region (attrs: confidence, boxes)   |
| `binary_sensor.<name>_crying`         | cry detected (attrs: score, last_cry)                         |
| `binary_sensor.<name>_connection_alarm` | camera lost **while monitoring**                            |
| `binary_sensor.<name>_camera_online`  | last snapshot succeeded                                       |
| `binary_sensor.<name>_monitoring`     | monitoring active                                             |
| `sensor.<name>_monitoring_mode`       | `off` / `auto` / `manual`                                     |
| `sensor.<name>_presence_confidence`   | %                                                             |
| `sensor.<name>_cry_score`             | %                                                             |
| `sensor.<name>_last_snapshot`         | timestamp                                                     |
| `switch.<name>_monitor`               | manual start/stop                                             |
| `switch.<name>_ai_presence_detection` | enable/disable AI                                             |
| `switch.<name>_cry_detection`         | enable/disable cry detection                                  |
| `switch.<name>_notifications`         | enable/disable notifications                                  |
| `select.<name>_view_mode`             | `snapshots` / `low` / `high`                                  |
| `number.<name>_snapshot_interval`     | seconds between snapshots                                     |
| `number.<name>_presence_threshold`    | 0 – 1                                                         |
| `number.<name>_cry_threshold`         | 0 – 1                                                         |
| `camera.<name>_live`                  | live camera (MJPEG/HLS in the selected quality)               |

### Services

- `babycam.start_monitoring` / `babycam.stop_monitoring` / `babycam.check_now` (optional `entry_id`).

### Events

- `babycam_baby_detected`, `babycam_cry_detected`, `babycam_connection_lost`.

### Audio

While monitoring, BabyCam transcodes the camera audio with ffmpeg to an MP3 stream at
`/api/babycam/<entry_id>/audio.mp3?token=…` and sends it to the configured media players with
`media_player.play_media`. The panel's **Listen** button plays the same stream in the browser.

## Automation example

```yaml
alias: Baby crying – flash the lights
trigger:
  - platform: state
    entity_id: binary_sensor.babycam_nursery_crying
    to: "on"
action:
  - service: light.turn_on
    target: { entity_id: light.living_room }
    data: { flash: short }
```

## Privacy

- All processing (snapshots, ONNX inference, audio transcoding) happens inside Home Assistant.
- The panel is plain JavaScript served by Home Assistant – no CDN, no external fonts or scripts.
- No model or asset is fetched at runtime; the only outbound traffic is to your camera.

## Development

```bash
pip install -r requirements_dev.txt
ruff check .
pytest
```

## License

MIT
