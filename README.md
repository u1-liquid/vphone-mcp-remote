# vphone-mcp

MCP server for programmatic control of [vphone-cli](https://github.com/Lakr233/vphone-cli) iOS VMs. Enables AI-driven E2E testing by exposing the VM's display, touch input, navigation, and the full vphone-cli command surface as MCP tools.

## How it works

```
Claude Code / Claude Desktop          (local: stdio · remote: HTTP + bearer token)
    │ MCP
    ▼
vphone-mcp (Python)
    ├── Unix socket (JSON)  → vphone-cli hostctl (running VM)
    └── subprocess           → vphone-cli (VM lifecycle, firmware)
    ▼
vphone-cli (Swift, vm/vphone.sock)
    │ Virtualization.framework
    ▼
iOS 26 VM
```

Every socket action returns a compact grayscale screenshot (~20-30KB) inline in the response, so the LLM can see what happened without a separate call. The 21 CLI wrapper tools forward vphone-cli's stdout/stderr faithfully.

## Setup

Requires [uv](https://github.com/astral-sh/uv) and a running vphone-cli VM with the host control socket enabled (PR [#261](https://github.com/Lakr233/vphone-cli/pull/261)).

```bash
git clone https://github.com/pluginslab/vphone-mcp.git
cd vphone-mcp
uv sync
```

### Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "vphone": {
      "command": "uv",
      "args": ["--directory", "/path/to/vphone-mcp", "run", "vphone-mcp"],
      "env": {
        "VPHONE_SOCK": "/path/to/vphone-cli/vm/vphone.sock"
      }
    }
  }
}
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vphone": {
      "command": "uv",
      "args": ["--directory", "/path/to/vphone-mcp", "run", "vphone-mcp"],
      "env": {
        "VPHONE_SOCK": "/path/to/vphone-cli/vm/vphone.sock"
      }
    }
  }
}
```

### Remote (HTTP) transport

The VMs only run on the Mac that hosts vphone-cli, but the agent does not have
to. Run the server there with a network transport and point remote clients at
it. A static bearer token read from the environment guards every endpoint — the
token never goes on the command line, so it stays out of `ps` output.

On the VM host:

```bash
export VPHONE_MCP_AUTH_TOKEN="$(openssl rand -hex 32)"   # keep this value
uv run vphone-mcp --transport http --host 0.0.0.0 --port 8765
```

From anywhere that can reach it:

```bash
claude mcp add --transport http vphone https://vphone.example.com/mcp \
  --header "Authorization: Bearer $VPHONE_MCP_AUTH_TOKEN"
```

Or in a client config file:

```json
{
  "mcpServers": {
    "vphone": {
      "type": "http",
      "url": "https://vphone.example.com/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Notes:

- **Authentication is mandatory** for network transports: the tools launch VMs
  and run vphone-cli with sudo, so the server refuses to start without
  `VPHONE_MCP_AUTH_TOKEN` (or `VPHONE_MCP_AUTH_TOKEN_FILE`). `--allow-anonymous`
  opts out explicitly, for when a tunnel or authenticating proxy already gates
  access.
- **Use TLS.** A bearer token over plain HTTP is a token in cleartext. Put the
  server behind a TLS-terminating reverse proxy or tunnel (Cloudflare Tunnel,
  Tailscale, nginx), or serve HTTPS directly with `--ssl-certfile`/`--ssl-keyfile`.
  The server prints a warning when it binds a non-loopback address without TLS.
- `GET /healthz` answers `{"status":"ok"}` without a token, for supervisors and
  proxy health checks. Nothing else is reachable unauthenticated.
- `--transport sse` serves the legacy SSE transport (`/sse` + `/messages/`) for
  older clients; both endpoints require the same token.
- Binding a non-loopback address disables the SDK's DNS-rebinding host allowlist
  (the token is the gate). Pass `--allowed-host vphone.example.com` to turn it
  back on for a known hostname.
- Paths in tool arguments (e.g. `screenshot(save_path=...)`, `vm_export(out=...)`)
  resolve on the **server** host, not the client's — see file access below.

#### Run it at login (launchd, macOS)

`contrib/launchd/install.sh` installs a LaunchAgent that serves
`0.0.0.0:8765` at login with one fixed token, and prints the client config:

```bash
./contrib/launchd/install.sh
```

The token is generated once into `~/.vphone/mcp-auth-token` (0600) and reused by
later re-installs, so configured clients keep working; the plist points
`VPHONE_MCP_AUTH_TOKEN_FILE` at it rather than carrying the value. Host, port,
public URL and the token itself are overridable from the environment — see
[contrib/launchd/README.md](contrib/launchd/README.md).

#### File access

Tools name files on the server's disk: the full-resolution screenshot, the
launch log, an exported `.vphone` archive. A remote client cannot read any of
them, so the server also exposes `GET /files/<path>`, where `<path>` is relative
to a vphone root:

```
http://<hash>:<mcp-token>@host:8765/files/screenshots/screen.png
       └ sha256 of the resolved absolute path
               └ the same token the MCP endpoint uses
```

- `screenshot()` and the background launch tools (`vm_launch`, `boot`) include a
  URL in their result; `get_file_url(path)` mints one for any other server-side
  path. `curl -O '<url>'` works as-is — HTTP Basic is used precisely because the
  credentials fit inside the URL.
- **No extra secret and no server state.** The Basic password is the MCP token
  you already configured, and the user is the hash that pins the link to one
  file. Serving a request means joining the relative path onto each root,
  normalising it, and answering only when the result stays inside that root
  **and** hashes to what the request presented. A tampered path — `../`, an
  absolute path, a different file — resolves elsewhere, so its hash no longer
  matches and it never reaches the filesystem. Links stay valid across restarts.
- Exactly one directory is served: `$VPHONE_ROOT/vphone-mcp` (default
  `~/.vphone/vphone-mcp`), which is this server's own — `logs/` and
  `screenshots/`. The VM library and the rest of the vphone root stay
  unreachable, so write anything a client must fetch there (e.g.
  `vm_export(out=...)`). Each file has exactly one URL: a `..` detour that
  normalises to the same file is a 404, and paths are resolved before the scope
  check, so a symlink inside the root pointing outside it is rejected.
- Set `VPHONE_MCP_PUBLIC_URL` when the server sits behind a proxy or binds
  `0.0.0.0`, otherwise the emitted links point at the bind address. Serve the
  whole thing over TLS: these URLs carry the token.
- `--no-files` turns the endpoint off entirely.

## Tools

### Hardware Keys
| Tool | Description |
|------|-------------|
| `go_home(screen=True, delay=500)` | Press home button |
| `press_power(screen=True, delay=500)` | Lock/wake the screen |
| `volume_up(screen=True, delay=500)` | Volume up |
| `volume_down(screen=True, delay=500)` | Volume down |

### Screenshots, Clipboard & Files
| Tool | Description |
|------|-------------|
| `screenshot(save_path=None, include_full_png=False)` | Full-res PNG + compact preview (returns embedded images); saves to `$VPHONE_ROOT/vphone-mcp/screenshots/screen.png` by default |
| `set_clipboard(text, screen=True, delay=500)` | Set the guest clipboard (NOT keyboard typing) |
| `get_file_url(path)` | URL for a server-side file, for remote clients (network transport only — see [File access](#file-access)) |

### Pre-mapped Navigation
| Tool | Description |
|------|-------------|
| `open_app(name)` | Open an app by name from the home screen |
| `tap_back` | Tap the iOS back button (top-left) |
| `scroll_down` | Scroll down on current screen |
| `scroll_up` | Scroll up on current screen |
| `open_notification_center` | Swipe down from top-left |
| `open_control_center` | Swipe down from top-right |
| `open_app_switcher` | Slow swipe up from bottom |
| `open_search` | Tap the home screen Search bar |
| `swipe_to_next_page` | Swipe to next home screen page |
| `swipe_to_previous_page` | Swipe to previous home screen page |

Supported app names for `open_app`: FaceTime, Calendar, Photos, Mail, Notes, Reminders, Clock, TV, Games, App Store, Maps, Health, Wallet, Settings, Phone, Safari, Messages, Music.

### Raw Interaction
| Tool | Description |
|------|-------------|
| `tap(x, y, screen=True, delay=500)` | Tap at pixel coordinates (1290x2796) |
| `swipe(x1, y1, x2, y2, duration_ms=300, screen=True, delay=500)` | Swipe between two points |

On all socket tools, `screen` controls the inline post-action screenshot (`screen=False` skips the capture) and `delay` is the settle time in ms before it. Every socket tool returns the status text plus the inline compact screenshot; when no VM is running they return a clean error message instead of a traceback.

### Environment Status
| Tool | Description |
|------|-------------|
| `vphone_status` | CLI binary path + PATH presence, resolved socket path + existence, `vm list --json` summary (parsed), running vphone-cli launch PIDs. Never raises. |

### vphone-cli Command Wrappers (21 tools)
| Tool | Description |
|------|-------------|
| `vm_list(json=False, library_root=None)` | List VM bundles (machine-readable with `json=True`) |
| `vm_info(name=None, json=False, library_root=None)` | Show a VM's configuration details |
| `vm_new(name, cpu=None, memory=None, disk_size=None, rom=None, seprom=None, library_root=None)` | Create an empty VM bundle |
| `vm_config(name=None, cpu=None, memory=None, network=None, bridge_interface=None, library_root=None)` | Adjust a VM's CPU/memory/network config |
| `vm_rename(name=None, new_name=None, library_root=None)` | Rename a VM (must be stopped) |
| `vm_delete(name=None, force=False, library_root=None, confirm=False)` | Delete a VM; `confirm=True` required unless `force=True` |
| `vm_clone(name=None, new_name=None, library_root=None)` | Clone a VM — the clone gets a fresh device identity, ideal for per-testcase VMs |
| `vm_export(name=None, out=<required>, max_=False, include_ipsw=False, library_root=None)` | Export a VM to a `.vphone` archive |
| `vm_import(input=<required>, name=None, library_root=None)` | Import a `.vphone` archive as a new VM |
| `vm_launch(name=None, dfu=False, headless=False, variant=None, no_vphoned=False, kernel_debug_port=None, project_root=None, library_root=None, verbose=0)` | Launch a VM — blocking process, runs in background with a log file |
| `vm_stop(name=None, timeout=None, library_root=None, confirm=False)` | Stop a running VM (SIGINT then SIGKILL) |
| `vm_create(name=<required>, variant=None, iphone_source=None, cloudos_source=None, disk_size=None, sudo_password=None, spoof_build=None, force_dsc_maxslide=False, frida=False, root_popup=False, interactive=False, keep_artifacts=False, project_root=None, verbose=0, library_root=None, confirm=False)` | End-to-end create pipeline (prepare→patch→restore→CFW→boot); needs internet + sudo, long-running |
| `fw_catalog(json=False)` | List firmware known to the cache |
| `fw_prepare(name=None, iphone_source=None, cloudos_source=None, iphone_version=None, iphone_build=None, list_=False, project_root=None, library_root=None, verbose=0)` | Prepare firmware (downloads IPSW/cloudOS, staged in cache) |
| `fw_patch(name=None, variant=None, force_exc_guard=False, frida=False, quiet=False, library_root=None)` | Patch a prepared firmware set |
| `restore(name=None, get_shsh=False, offline=False, udid=None, ecid=None, project_root=None, library_root=None, verbose=0, confirm=False)` | Restore a VM from prepared firmware |
| `cfw_install(name=None, variant='exp', spoof_build=None, force_dsc_maxslide=False, use_sudo=False, keep_artifacts=False, project_root=None, library_root=None, verbose=0, confirm=False)` | Install custom firmware; uses `--root-popup` by default, `use_sudo=True` for the plain sudo re-exec |
| `patch_firmware(vm_directory=<required>, variant=None, records_out=None, quiet=False, no_binpack=False, no_vphoned=False, force_exc_guard=False, frida=False)` | Patch a VM directory's firmware in place |
| `patch_component(component=<required>, input=<required>, output=<required>, quiet=False, records_out=None, target_os=None, frida=False)` | Patch a single firmware component (txm/kernel-base/kernel-jb) |
| `setup_env(force=False, project_root=None)` | Provision the vphone Python environment (~/.vphone/venv) |
| `boot(config=<required>, dfu=False, headless=False, kernel_debug_port=None, vphoned_bin=None, variant=None, install_ipa=None, no_vphoned=False)` | Boot from a raw config.plist manifest — blocking process, runs in background with a log file |

Each wrapper maps 1:1 onto a vphone-cli subcommand (flags use canonical long forms, e.g. `-d/--dfu` is passed as `--dfu`) and reports exit code, argv, stdout, and stderr — a nonzero exit is returned as text for the LLM to read, not an exception. Destructive/privileged tools (`vm_delete`, `vm_stop`, `vm_create`, `restore`, `cfw_install`) require `confirm=True`; `vm_delete` also accepts `force=True` to skip the gate (mirrors the CLI's `-f`). Long-running pipeline tools have generous timeouts and only raise on timeout. `vm_launch` and `boot` are blocking VM processes that run detached, logging to `$VPHONE_ROOT/vphone-mcp/logs/<name>_launch_<epoch>.log`; `sudo_password` for `vm_create` falls back to `VPHONE_SUDO_PASSWORD` so it can stay out of agent context.

### Not exposed (hostctl socket limitation)

The hostctl socket only implements the 5 commands above — there is no guest control channel for: files, apps, keychain, settings, accessibility tree, and clipboard-get (reading the guest clipboard). These would require an upstream vphone-cli extension and are out of scope.

## Example session

```
User: Open Settings and navigate to General > About

Claude: [calls open_app("Settings")]
        → sees Settings list
        [calls tap(400, 1880)]
        → sees General page
        [calls tap(400, 1100)]
        → sees About page with iOS 26.1, Serial: vphone-1337
```

## Configuration

| Env var | Description |
|---------|-------------|
| `VPHONE_SOCK` | Path to `vphone.sock` (auto-discovered if not set) |
| `VPHONE_CLI_BIN` | Path to the vphone-cli binary (default: PATH lookup, then /Applications/vphone-cli.app) |
| `VPHONE_LIBRARY_ROOT` | VM library root override (default `~/.vphone/VMs`) |
| `VPHONE_ROOT` | vphone project root override (default `~/.vphone`) |
| `VPHONE_SUDO_PASSWORD` | sudo password fallback for `vm_create` — keeps it out of agent transcripts |

### Transport / remote access

Every setting below has a matching CLI flag (`vphone-mcp --help`); flags win over env vars.

| Env var | Flag | Description |
|---------|------|-------------|
| `VPHONE_MCP_TRANSPORT` | `--transport` | `stdio` (default), `streamable-http` (alias `http`), or `sse` |
| `VPHONE_MCP_AUTH_TOKEN` | — | Bearer token required by network transports. Deliberately env-only so it never appears in argv |
| `VPHONE_MCP_AUTH_TOKEN_FILE` | — | File to read the token from when `VPHONE_MCP_AUTH_TOKEN` is unset (launchd/docker secrets) |
| `VPHONE_MCP_HOST` | `--host` | Bind address (default `127.0.0.1`; `0.0.0.0` to accept remote clients) |
| `VPHONE_MCP_PORT` | `--port` | Bind port (default `8765`) |
| `VPHONE_MCP_PATH` | `--path` | Streamable-HTTP endpoint path (default `/mcp`) |
| `VPHONE_MCP_ALLOW_ANONYMOUS` | `--allow-anonymous` | Serve without a token — only when something else gates access |
| `VPHONE_MCP_JSON_RESPONSE` | `--json-response` | Plain JSON replies instead of SSE streams (for SSE-buffering proxies) |
| `VPHONE_MCP_STATELESS` | `--stateless` | New transport per request — no session affinity, for load-balanced setups |
| `VPHONE_MCP_ALLOWED_HOSTS` | `--allowed-host` | Comma-separated Host allowlist (re-enables DNS-rebinding protection) |
| `VPHONE_MCP_ALLOWED_ORIGINS` | `--allowed-origin` | Comma-separated Origin allowlist |
| `VPHONE_MCP_SSL_CERTFILE` | `--ssl-certfile` | TLS certificate for direct HTTPS serving |
| `VPHONE_MCP_SSL_KEYFILE` | `--ssl-keyfile` | TLS private key for direct HTTPS serving |
| `VPHONE_MCP_PUBLIC_URL` | `--public-url` | External base URL used to build file links (set it behind a proxy or a wildcard bind) |
| `VPHONE_MCP_DISABLE_FILES` | `--no-files` | Do not serve `/files/<path>` at all |

## License

MIT
