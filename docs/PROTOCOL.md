# Tactiq wire protocol

The vocabulary shared by the BLE firmware, the Android bridge, and the web
demo. Command tokens are the snake_case strings defined in
`host/tactiq/grammar.py` — one source of truth:

`confirm` `back` `undo` `next` `read` `previous` `quick_action_1`
`quick_action_2` `emergency`

## BLE (step 8)

- Device name: **Tactiq Ring**
- Service: Nordic UART Service (NUS)
  - Service UUID `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
  - TX (ring → phone, notify) `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`
  - RX (phone → ring, write)  `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
- Optional second profile (iOS route, §3.4): HID-over-GATT keyboard
  presenting VoiceOver QuickNav key chords. Enabled with `TACTIQ_HID 1`
  in the firmware; the fixed command set only, as the paper promises.

## TX lines (newline-terminated ASCII)

| Line | Meaning |
|---|---|
| `TOK,<token>,<contact_key>,<t_ms>,<dur_ms>` | decoded command token |
| `GATE,armed,<t_ms>` / `GATE,idle,<t_ms>` | activation gate transitions (P9) |
| `STA,hello,<fw_version>` | sent on connect |
| `STA,battery,<pct>` | periodic (stub until fuel-gauge hardware) |
| `STA,pong` | reply to `PING` |

Example: `TOK,confirm,index_tip,183422,161`

## RX lines

| Line | Meaning |
|---|---|
| `PING` | liveness check → `STA,pong` |

## Haptic classes (step 10, P3/P4)

Deliberately few (P4 — users distinguish only a handful of patterns):

| Class | When | DRV2605 effect (default) |
|---|---|---|
| `armed` | gate opened | 24 — sharp tick |
| `confirm` | token accepted | 1 — strong click |
| `reject` | indeterminate press dropped | 10 — double click |
| `emergency` | emergency fired at the 5 s mark | 15 — 750 ms alert |
