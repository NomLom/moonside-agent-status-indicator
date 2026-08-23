# Moonside Agent Status Lamp

Turns a Moonside O101 BLE lamp into a Claude Code status lamp.

This repo also retains a BK-Light compatibility path for the older matrix-panel runner, but the primary target now is Moonside. It can additionally mirror the same status files to a reserved BlinkStick LED without replacing the Moonside lamp.

What changed from the original Claude Code project:
- Moonside transport is now built in for the O101 lamp
- the default state-file path is still article-style: `/tmp/claude_code_status`
- the preferred config key is `claude_status:`
- the preferred env vars are `CLAUDE_CODE_STATUS_DIR` and `MOONSIDE_ADDRESS`
- Hermes plugin/API bridge support is still available as optional compatibility
- optional BlinkStick renderer reserves one selected USB LED while leaving other LEDs alone
- legacy `AGENT_STATUS_DIR`, `BK_LIGHT_STATUS_DIR`, and `BK_LIGHT_ADDRESS` are still accepted

States
- idle -> warm white
- thinking -> working animation
- tool_use -> working animation
- permission -> input animation
- success -> success color/effect
- failed -> failed color/effect
- cancelled -> off

How it works
1. Claude Code hooks observe session/tool/approval lifecycle.
2. The hook writes one state file per active session into `/tmp/claude_code_status`.
3. `python run.py` watches those files and drives the lamp.
4. Hermes plugin/API bridge can write the same state format if you want that later.

Requirements
- Python 3.11+
- Moonside O101 lamp (for the Moonside renderer)
- BlinkStick USB LED (optional, for the BlinkStick renderer)
- Claude Code
- BLE support for the host

Repository layout
- `scripts/claude_status_hook.py`: Claude Code hook entrypoint
- `scripts/install_claude_hooks.sh`: installs Claude Code hooks into `~/.claude/settings.json`
- `plugin.yaml` + `__init__.py`: optional Hermes plugin entrypoint
- `agent_status/hermes_plugin.py`: optional Hermes hook bridge
- `agent_status/api_bridge.py`: optional Hermes API/SSE bridge
- `agent_status/moonside.py`: Moonside BLE transport and state mapping
- `agent_status/runner.py`: lamp watcher / renderer
- `blinkstick_status.py`: USB BlinkStick renderer that consumes the same state files
- `scripts/install_blinkstick_service.sh`: installs the BlinkStick renderer as a persistent user service
- `systemd/blinkstick-status.service.in`: generated user-service template
- `udev/85-blinkstick.rules`: least-privilege USB access rule for local `plugdev` users
- `scripts/install_hermes_plugin.sh`: symlink this repo into `~/.hermes/plugins/` and enable it
- `scripts/status_file_hook.py`: generic hook-driven JSON -> state-file bridge

Install
```bash
git clone --recurse-submodules <your-repo-url>
cd moonside-agent-status-indicator

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
bash scripts/install_claude_hooks.sh
```

If you prefer to configure Claude Code manually, add the command printed by `scripts/install_claude_hooks.py` to the relevant hook entries in `~/.claude/settings.json`.

Optional Hermes plugin install:
```bash
bash scripts/install_hermes_plugin.sh
```

Lamp config
Edit `config.local.yaml` or `config.yaml`:
```yaml
device:
  address: "04:B2:47:8C:E1:F6"

claude_status:
  status_dir: "/tmp/claude_code_status"
  stale_threshold: 3600
  statuses:
    idle: "😴"
    thinking: "🧠"
    tool_use: "⚙️"
    permission: "🔔"
    success: "✅"
    failed: "❌"
    cancelled: "⏹️"
```

Run the lamp
```bash
source .venv/bin/activate
python run.py
```

## Optional BlinkStick status LED

The BlinkStick renderer consumes the same normalized state files as the Moonside lamp. It reserves only LED index `0` by default; it does not set any other BlinkStick LEDs.

Default mapping:
- `thinking` and `tool_use` -> blue (`0, 0, 255`)
- `permission` -> a two-tone purple pulse (`214, 0, 255` / `120, 0, 117`)
- `success` -> muted green (`0, 100, 30`)
- `failed` -> dark red (`130, 0, 0`)
- `cancelled` -> off
- `idle` -> dim amber (`120, 55, 0`)
- 60 minutes of continuous idle -> off

### USB permission rule

Linux requires a udev rule for unprivileged USB access. The bundled rule permits only members of the local `plugdev` group; it does not make the device world-writable.

```bash
sudo install -o root -g root -m 0644 udev/85-blinkstick.rules /etc/udev/rules.d/85-blinkstick.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=20a0 --attr-match=idProduct=41e5
```

### Install the persistent renderer

```bash
bash scripts/install_blinkstick_service.sh
```

This generates and enables `~/.config/systemd/user/blinkstick-status.service`. Override the defaults at installation time if required:

```bash
bash scripts/install_blinkstick_service.sh \
  --status-dir /tmp/claude_code_status \
  --led-index 0 \
  --idle-timeout-seconds 3600
```

Check it with:

```bash
systemctl --user status blinkstick-status.service
```

For a one-shot hardware test without installing a service:

```bash
.venv/bin/python blinkstick_status.py --status-dir /tmp/claude_code_status --led-index 0 --once
```

Optional Hermes API bridge
Use this only when you are driving Hermes through its API server and want richer state transitions.

Create a run and mirror its state to the lamp files:
```bash
python3 -m agent_status.api_bridge run   --base-url http://127.0.0.1:8642   --api-key "$API_SERVER_KEY"   --session-id lamp-demo   "Use the terminal tool to run `hostname` and then reply DONE."
```

Attach to an existing run id:
```bash
python3 -m agent_status.api_bridge watch <run_id>   --base-url http://127.0.0.1:8642   --api-key "$API_SERVER_KEY"   --session-id lamp-demo
```

Find the lamp address
```bash
bluetoothctl --timeout 10 scan on
```

Claude Code hook mapping
- `SessionStart` -> idle
- `UserPromptSubmit` -> thinking
- `PreToolUse` -> tool_use
- `PostToolUse` -> thinking
- `PostToolUseFailure` -> thinking
- `PermissionRequest` -> permission
- `SubagentStart` -> tool_use
- `SubagentStop` -> thinking
- `SessionEnd` -> remove state file

Hermes hook mapping used by the optional plugin
- `on_session_start` -> idle
- `pre_llm_call` -> thinking
- `pre_tool_call` -> tool_use
- `post_tool_call` -> thinking (or stays tool_use if nested)
- `pre_approval_request` -> permission
- `post_approval_response` -> thinking / tool_use
- `post_llm_call` -> idle
- `on_session_end` -> idle
- `on_session_finalize` / `on_session_reset` -> remove state file

Manual tests
Write a fake state file:
```bash
mkdir -p /tmp/claude_code_status
echo thinking > /tmp/claude_code_status/test-session-1
```

Test the Claude hook script:
```bash
echo '{"hook_event_name":"PreToolUse","session_id":"test-1","tool_name":"Bash"}'   | python3 scripts/claude_status_hook.py
cat /tmp/claude_code_status/test-1
```

Optional Hermes plugin test without the lamp:
```bash
rm -rf /tmp/claude_code_status
hermes -z "Use the read_file tool on /etc/hostname and then tell me the hostname." --toolsets file
find /tmp/claude_code_status -maxdepth 1 -type f -print -exec cat {} \;
```

Approval test
Run a command that requires approval without `-z`/`--yolo`, for example in interactive Hermes:
```text
run `rm -rf /tmp/definitely-not-real` and deny it
```
The lamp should flip to `permission` while Hermes waits.

Notes
- Claude Code hooks are the primary path.
- The Hermes API/SSE bridge can surface richer live states, including `success`, `failed`, and `cancelled`.
- Preferred env vars are `CLAUDE_CODE_STATUS_DIR` and `MOONSIDE_ADDRESS`.
- Legacy env vars `AGENT_STATUS_DIR`, `BK_LIGHT_STATUS_DIR`, and `BK_LIGHT_ADDRESS` are still accepted.
- The runner accepts both `claude_status:` and `agent_status:` config blocks.
- The BK-Light submodule remains only for the legacy panel path.

Troubleshooting
- Hook not firing: check `~/.claude/settings.json` and confirm it points at `scripts/claude_status_hook.py`.
- No state files: test the hook manually with the JSON example above.
- Lamp not updating: verify the BLE address and keep the lamp powered and in range.
- Emoji missing on Linux: install `fonts-noto-color-emoji`.

Acknowledgements
Built on top of `Bk-Light-AppBypass` for the legacy matrix path and inspired by the original Claude Code lamp experiments.
