# PR: Wake-on-LAN control before SD payload delivery

## Summary

This change adds an optional Wake-on-LAN (WOL) flow to AIYA so the bot can wake a remote high-performance Stable Diffusion host before sending `/sdapi` payloads.

The feature is fully controlled by environment variables and is disabled by default.

## Problem

In split deployments, AIYA can run on a low-power machine while Stable Diffusion runs on a dedicated high-performance machine.
If the high-performance host is sleeping, SD API requests fail until it is manually resumed.

## Solution

Implemented WOL support inside the existing request interception path in `core/stablecog.py` (where requests are already monkeypatched).

Before sending matching requests:

1. Validate that WOL is enabled and request matches target rules.
2. Send magic packet to the configured MAC/broadcast/port.
3. Wait a configurable boot window before sending payload.
4. Use cooldown and "awake grace" windows to avoid repeated wake packets and avoid waiting on every request.

## Environment variables

```dotenv
SD_WAKE_ON_LAN_ENABLED = "True"
SD_WAKE_ON_LAN_MAC = "34:5A:60:36:58:09"
SD_WAKE_ON_LAN_BROADCAST_IP = "192.168.100.255"
SD_WAKE_ON_LAN_PORT = 9
SD_WAKE_ON_LAN_BOOT_WAIT_S = 12
SD_WAKE_ON_LAN_COOLDOWN_S = 45
SD_WAKE_ON_LAN_AWAKE_GRACE_S = 900
SD_WAKE_ON_LAN_ONLY_SDAPI = "True"
# SD_WAKE_ON_LAN_TARGET_URL = "http://192.168.100.10:7860"
```

## Files changed

- `core/stablecog.py`
  - Added WOL packet generation and UDP broadcast send helpers.
  - Added env parsing for WOL settings.
  - Added request matching by endpoint and target URL base.
  - Added wake/cooldown/boot-wait/awake-grace state handling.
  - Integrated wake check into both `requests.post` and `requests.sessions.Session.post` patches.
- `README.md`
  - Added documentation section: "Optional Wake-on-LAN before SD API payloads".

## Backward compatibility

- Default behavior unchanged (`SD_WAKE_ON_LAN_ENABLED=False`).
- If misconfigured (missing MAC/invalid port), feature self-disables and logs warnings without crashing request flow.

## Validation performed

- `python -m compileall core/stablecog.py` succeeded.
- Verified no API contract changes for existing commands.
- Verified docs include setup and behavior notes.

## Risks / considerations

- Wake-on-LAN depends on network/LAN broadcast routing and motherboard/NIC BIOS settings.
- `SD_WAKE_ON_LAN_BOOT_WAIT_S` may need tuning per hardware boot/resume time.
- WOL is currently applied to matching `/sdapi` POST requests in the patched request path.

## Suggested QA checklist

- [ ] With WOL disabled, generation path behaves exactly as before.
- [ ] With WOL enabled and host sleeping, first request wakes host and succeeds after boot wait.
- [ ] Multiple quick requests do not spam wake packets (cooldown respected).
- [ ] Requests during active session do not keep waiting unnecessarily (awake grace respected).
- [ ] Non-target URLs are not affected.
