# [infra] Tower inference server — GPU ollama over WireGuard, VPS stays the edge

## Context / goal

The production VPS turned out to have **2 GB RAM, not 4**. That is enough to run the JAC web
stack (Django/ASGI + Celery + Postgres + Redis + nginx) but **not** enough to also host an ollama
model — the co-resident peak (ollama with a model loaded *plus* the Celery worker holding the whole
Django app in RAM during an embedding/generation pass) blows past 2 GB and lands on the OOM killer
exactly when a user is waiting on a run.

The fix is a split we do **not** have to invent — the codebase was built for it.
`llm_connector/validators.py` documents *"Self-hosting Ollama over a VPN is a first-class use
case (the zero-cost thesis)"*, and `settings.py` already carries a Tailscale example in the
`LLM_URL_ALLOWLIST` comment. So:

- **VPS** stays the **only internet-facing surface** — the JAC app, hardened as it already is
  ("treat every authed surface as internet-facing"). No model on it.
- **Tower** (spare Ryzen 5 / RTX 3070 8 GB / 32 GB RAM) becomes a **Linux GPU inference box**
  that exposes **only ollama, only over a private WireGuard tunnel, only to the VPS**. With 8 GB
  VRAM the *default* model jumps from `llama3.2:1b` to a real 7–8B model.

This is the safer of the two shapes we discussed: the VPS is a point-to-point WireGuard peer of the
tower, **not** a reverse-proxy doorway into the home LAN. If the VPS is ever compromised the blast
radius into the home is one TCP port (ollama) on one host, not the whole app + network.

**Roadmap:** new infra prerequisite for the JAC deployment / generation loop (see
[[generation-async-loop]], [[project-purpose-cv-showcase]]). Not a code feature — the JAC-critical
behaviour it leans on (`validate_safe_llm_url` honouring a VPN CIDR allowlist) is already shipped
and tested (`test_validators.py:75`).

### Decisions already made (this session)

- **No Windows.** Full Linux on the tower → no GPU passthrough / VFIO. Games run natively
  (Steam/Proton); Affinity is deferred (browser version exists, intern-free for ~6 months).
- **Distro: Pop!_OS (NVIDIA ISO).** Services run in Docker (distro-agnostic), so pick the distro
  for the finicky part — gaming + NVIDIA. Pop!_OS bakes the NVIDIA driver into the installer, is
  Ubuntu-LTS-stable (this box is a 24/7 server first), games well, and has a huge community.
  *Alternative:* **Nobara** if you want gaming tuned to the max. The distro choice only affects the
  "GPU driver" step below.
- **Two VPNs, kept strictly separate** (see the companion section): **Mullvad** = outbound
  download traffic, lives *inside* a gluetun container. **Our WireGuard (`wg0`)** = JAC↔ollama,
  a host interface routing *only* the VPS. Neither routes the other; the tower's default route
  stays the plain home gateway.

---

## Affected files

Almost all of this is **ops on two machines** — there is no application source to type. The only
repo-touching change is environment configuration on the VPS, plus one test file.

| path | change | why |
| --- | --- | --- |
| *(tower)* `/etc/wireguard/wg0.conf` | new | tower's WireGuard peer (dials the VPS) |
| *(tower)* `/etc/systemd/system/ollama.service.d/override.conf` | new | bind ollama for tunnel reach + GPU keep-alive tuning |
| *(tower)* ufw rules | new | deny-by-default; ollama reachable only from the VPS's wg IP |
| *(VPS)* `/etc/wireguard/wg0.conf` | new | VPS WireGuard peer (listens; the tunnel's server side) |
| *(VPS)* JAC deploy env (`.env` / systemd `Environment=`) | edit | repoint the default alias at the tower + allowlist the tunnel subnet |
| `backend/llm_connector/tests/test_config.py` | edit | model-level contract guard: an ollama row at the tower's wg IP validates iff the operator allowlists the tunnel subnet (**green on arrival** — see Tests) |

**No Python/JS source is edited.** `LLM_URL`, `LLM_MODEL`, `LLM_STRENGTH`, `LLM_URL_ALLOWLIST` are
all already read from the environment in `settings.py:185-210`.

---

## The plan (in build order)

Pick a WireGuard subnet that does **not** collide with your home LAN (often `192.168.x`) or any
VPS-internal range. This guide uses **`10.10.0.0/24`**, VPS = `10.10.0.1`, tower = `10.10.0.2`.
Change it everywhere if it clashes.

### 0. Prep the tower (Pop!_OS)

1. Download the **Pop!_OS *NVIDIA* ISO** (System76). Flash with Balena Etcher / `dd`, install,
   wipe Windows (you decided against dual-boot).
2. First boot, update:
   ```bash
   sudo apt update && sudo apt full-upgrade -y && sudo reboot
   ```
3. Confirm the GPU + driver (the NVIDIA ISO ships it):
   ```bash
   nvidia-smi   # must print the RTX 3070 and a driver version
   ```
   If `nvidia-smi` fails, the driver didn't take — `sudo apt install system76-driver-nvidia` then
   reboot. (On Nobara instead: driver is preinstalled; verify the same way.)
4. Give the box a **static LAN IP** (router DHCP reservation is simplest) so the VPS endpoint and
   your SSH habits don't chase a moving address.
5. SSH in from your workstation for the rest (`ssh tower.local` or the reserved IP).

> CUDA note: ollama's native binary links its own CUDA runtime and talks to the driver directly —
> you do **not** need the full `cuda-toolkit`. A working `nvidia-smi` is the whole requirement.

### 1. Install ollama on the GPU + pull the bigger default

```bash
curl -fsSL https://ollama.com/install.sh | sh   # installs + enables the systemd service
```

Confirm it saw the GPU:
```bash
journalctl -u ollama --no-pager | grep -i -m1 "gpu\|cuda\|compute"   # should mention CUDA / the GPU
```

Pull the models. On 8 GB VRAM a **7B chat model at Q4 (~4.7 GB)** fits comfortably alongside the
0.6B embedder (~0.6 GB) — ~5.3 GB resident, leaving headroom for context/KV cache:

```bash
ollama pull qwen2.5:7b-instruct     # writer + instruct/conversational rungs; strong follower
ollama pull qwen3-embedding:0.6b    # unchanged — the ranking floor (see [[project_jac]])
```

*Alt chat model:* `llama3.1:8b` (~4.9 GB) if you prefer Llama. Keep the **embedder as-is** — it's
the CV-selection floor and there's no reason to change it here.

Quick local smoke (still bound to localhost at this point):
```bash
ollama run qwen2.5:7b-instruct "one sentence: why hire a versatile engineer?"
nvidia-smi   # during generation you should see the ollama process holding VRAM
```

### 2. WireGuard — point-to-point VPS ↔ tower

The **VPS has the public IP** and is the tunnel's listener; the **tower is behind home NAT** and
dials out (with keepalive to hold the NAT mapping). Install on both:

```bash
sudo apt install -y wireguard        # both machines
```

Generate a keypair on **each** machine:
```bash
umask 077
wg genkey | tee privatekey | wg pubkey > publickey
cat privatekey publickey             # note both; you'll cross-paste the PUBLIC keys
```

**VPS** — `/etc/wireguard/wg0.conf`:
```ini
[Interface]
Address = 10.10.0.1/24
ListenPort = 51820
PrivateKey = <VPS_PRIVATE_KEY>
# Host-to-host tunnel — NOT a gateway. No PostUp NAT/forwarding on purpose.

[Peer]
# tower
PublicKey = <TOWER_PUBLIC_KEY>
AllowedIPs = 10.10.0.2/32
```

**Tower** — `/etc/wireguard/wg0.conf`:
```ini
[Interface]
Address = 10.10.0.2/24
PrivateKey = <TOWER_PRIVATE_KEY>

[Peer]
# VPS
PublicKey = <VPS_PUBLIC_KEY>
Endpoint = <VPS_PUBLIC_IP>:51820
AllowedIPs = 10.10.0.1/32       # ← ONLY the VPS goes through the tunnel.
PersistentKeepalive = 25        #   keeps this from becoming the tower's default route,
                                #   so home internet + Mullvad are untouched (two-VPN separation).
```

Bring it up and enable at boot on **both**:
```bash
sudo systemctl enable --now wg-quick@wg0
sudo wg show                    # after both are up: a handshake + a peer endpoint
```

Verify the tunnel from the **VPS**:
```bash
ping -c3 10.10.0.2              # reaches the tower over wg0
```

### 3. Bind ollama to the tunnel + firewall it

By default ollama listens on `127.0.0.1:11434` — unreachable over the tunnel. Bind it to all
interfaces **and** lock it down with the firewall (binding `0.0.0.0` + a strict ufw source rule is
robust against boot-ordering; the firewall, not the bind address, is the real boundary):

```bash
sudo systemctl edit ollama
```
Add:
```ini
[Service]
Environment=OLLAMA_HOST=0.0.0.0:11434
# Optional: unload the model when idle so VRAM frees up before a rare gaming session.
Environment=OLLAMA_KEEP_ALIVE=30m
```
```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

Firewall (tower) — **deny by default, ollama only from the VPS's wg IP**:
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 10.10.0.1 to any port 11434 proto tcp   # ← only the VPS, only over wg
sudo ufw allow from 192.168.0.0/16 to any port 22 proto tcp # SSH from home LAN (adjust to your subnet)
sudo ufw enable
sudo ufw status verbose
```
> ollama has **no auth of its own — the network boundary IS the auth.** The ufw rule above means
> even other machines on your home LAN cannot reach `:11434`; only the VPS, only through `wg0`.
> The tower needs **no inbound WireGuard port** (it dials out). On the **VPS**, open the listener:
> `sudo ufw allow 51820/udp` (keep your existing 22/80/443).

Prove ollama is reachable over the tunnel, from the **VPS**:
```bash
curl -s http://10.10.0.2:11434/api/tags | head        # lists the models you pulled
```

### 4. Repoint JAC (on the VPS)

Set these in the JAC deploy environment (the `.env` or systemd `Environment=` lines that feed
**both** the Django process and the Celery worker):

```bash
LLM_URL=http://10.10.0.2:11434/v1     # tower over wg0 — mirror the existing "/v1" path shape,
                                       # only the host changes (see settings.py:188)
LLM_MODEL=qwen2.5:7b-instruct          # the bigger default (was llama3.2:1b)
LLM_STRENGTH=standard                  # ← REQUIRED to actually use the bigger model (see gotcha)
LLM_URL_ALLOWLIST=10.10.0.0/24         # so a per-user ollama row at the tower also validates
# LLM_EMBED_MODEL — leave as qwen3-embedding:0.6b
```

> **Gotcha — the model bump alone does nothing to the rung.** `settings.py:193` sets
> `"strength": os.getenv("LLM_STRENGTH", "light")`, and in `get_alias_strength()` an *explicit*
> strength **wins over autodetect** (`conf.py:119-122`). So without `LLM_STRENGTH=standard` the
> default alias stays pinned to the **light** (embedding-only) rung no matter how big the model is.
> Set it to `standard` to unlock the instruct rung; `strong` is available too but a 7–8B model
> isn't honestly a "strong" model — `standard` is the right rung for it.

> **Product call, make it on purpose.** Moving the *default* off `light` changes what the default
> **showcases**. Per [[project-purpose-cv-showcase]] the `light` rung is a deliberate demonstration
> that *small self-hosted models are viable*. A 7–8B default on your own GPU is arguably a stronger
> version of the same thesis (consumer hardware vs. a $20k cloud bill), but it *is* a different
> claim. Decide whether the public default should be `light` (the showcase) or `standard` (better
> letters). You can also leave the site default at `light` and pin your **own** account to a
> `standard` tower alias via the LLM-config tab — best of both.

The SSRF validator does **not** gate the operator-set `LLM_URL` default (it's trusted; only
user-saved `custom`/`ollama` rows are validated — `models.py:80-85`). `LLM_URL_ALLOWLIST` is here so
that when *you* save a personal ollama alias pointing at `10.10.0.2` from the account LLM tab, it
passes `clean()`. (`LLM_URL_ALLOW_PRIVATE=True` is the blunter alternative — trusts the whole
private net; prefer the specific `/24`.)

Restart both processes:
```bash
sudo systemctl restart jac-web jac-worker   # or however the VPS runs Django + Celery
```

---

## Companion section — the tower's co-tenant services

Right-sized on purpose: enough architecture to make the distro/resource decisions sound and avoid
the two-VPN trap. Each of these earns its **own** `/setup-guide` when you build it — this is not the
step-by-step.

### arr stack behind Mullvad (Docker Compose)

Standard pattern: a **gluetun** container is the network namespace for the download client, and
everything that must exit via Mullvad joins gluetun's network. gluetun has a built-in **killswitch**
— if the Mullvad tunnel drops, traffic stops rather than leaking your home IP.

```yaml
# skeleton only — a full guide comes later
services:
  gluetun:
    image: qmcgaw/gluetun
    cap_add: [NET_ADMIN]
    environment:
      VPN_SERVICE_PROVIDER: mullvad
      VPN_TYPE: wireguard
      WIREGUARD_PRIVATE_KEY: <from-your-mullvad-account>
      WIREGUARD_ADDRESSES: <from-mullvad>
      SERVER_CITIES: Amsterdam
    ports: ["8080:8080"]        # qbittorrent UI, published via gluetun
  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent
    network_mode: "service:gluetun"   # ← all its traffic exits through Mullvad
  prowlarr:  { image: lscr.io/linuxserver/prowlarr }   # indexers
  sonarr:    { image: lscr.io/linuxserver/sonarr }     # TV
  radarr:    { image: lscr.io/linuxserver/radarr }     # movies
  # sonarr/radarr/prowlarr stay on the normal bridge net — only the *download client* needs Mullvad.
```

> **Two-VPN separation — the thing that bites people.** Mullvad lives **inside gluetun** and only
> carries qBittorrent. Our `wg0` is a **host** interface with `AllowedIPs = 10.10.0.1/32`, so it
> carries **only** VPS↔ollama and never becomes the default route. The tower's own internet keeps
> going out the plain home gateway. Three independent paths, zero overlap. If you ever see JAC
> latency spike or the tower's public IP change, check that neither VPN swallowed the default route.

### Jellyfin — "softbox for phone" *(assumption — confirm)*

Best read of "softbox for phone" = a **media server** streaming the arr library to your phone, using
the 3070's **NVENC** for hardware transcode. Docker with the NVIDIA runtime + `/dev/dri`. If you
actually meant a **WireGuard road-warrior** (phone → home services) or **Nextcloud photo-sync**, say
so and this slot retargets — it's independent of the JAC spine either way.

### Gaming (native)

Pop!_OS: install Steam, enable **Proton** (Steam → Settings → Compatibility → "Enable Steam Play for
all titles"), optionally `gamemode`. Caveat: kernel-level anti-cheat titles may not run under Proton
— check ProtonDB for the specific games.

### GPU / VRAM contention — the one real constraint of sharing 8 GB

One 3070, 8 GB, shared cooperatively (no passthrough, so no hard partition):

- **7B model resident ≈ 5 GB.** A modern game wants **6–8 GB**. They don't both fit.
- Because gaming is *rare* and JAC has **no uptime requirement**, the answer is time-sharing, not
  more hardware: `OLLAMA_KEEP_ALIVE=30m` (set above) unloads the idle model so VRAM is free by the
  time you launch a game, or `ollama stop qwen2.5:7b-instruct` to free it immediately.
- Jellyfin NVENC + a game + ollama all contend; realistically **one heavy GPU user at a time**.
  Fine for a single-operator home box.

---

## Tests

**This guide writes no new implementation, so there is no red→green unit test to land** — the
JAC-critical behaviour (the SSRF validator honouring a VPN-CIDR allowlist) is already implemented
(`validators.py`) and already tested (`test_validators.py:63-95`, incl. the exact CIDR-member case).
The guide's real acceptance criteria are the **Verification** steps below (a live generation running
on the tower's GPU over the tunnel).

What I *did* add is a **model-level contract guard** for the one action you'll actually take —
saving an `LLMConfig` row (provider `ollama`) pointing at the tower's wg IP — exercised through the
model's `clean()` → validator wiring, which the existing validator-function tests don't cover:

- `backend/llm_connector/tests/test_config.py` → `LLMConfigModelTests`
  - `test_ollama_row_at_allowlisted_vpn_ip_validates` — `full_clean()` passes for
    `http://10.10.0.2:11434/v1` when `LLM_URL_ALLOWLIST=["10.10.0.0/24"]`.
  - `test_ollama_row_at_private_ip_rejected_without_allowlist` — the same row is rejected with an
    empty allowlist (the deny-by-default that keeps this safe).

> ⚠️ **These start GREEN, not red.** The wiring they assert (`models.clean()` calling
> `validate_safe_llm_url`, `models.py:80-85`) already exists, so there's nothing to implement red.
> They are a **regression guard** documenting the deployment contract — if a future refactor drops
> the validator call from `clean()`, or someone tightens the allowlist matcher, these fail loudly.
> That's their job, and it's why the guide's "did it work" lives in Verification, not here.

Run them:
```bash
cd backend && python manage.py test llm_connector.tests.test_config -v2
```

---

## Verification

End-to-end, in order. "Done" = a real JAC generation runs on the tower's GPU, over the tunnel, with
the bigger model.

1. **GPU is live (tower):** `nvidia-smi` prints the RTX 3070.
2. **ollama uses the GPU (tower):** during `ollama run qwen2.5:7b-instruct "hi"`, a second-terminal
   `nvidia-smi` shows the ollama process holding VRAM (not a CPU-only fallback).
3. **Tunnel up (VPS):** `sudo wg show` shows a recent handshake; `ping -c3 10.10.0.2` succeeds.
4. **ollama reachable only over wg (VPS):** `curl -s http://10.10.0.2:11434/api/tags` lists your
   models. From a **third machine on the home LAN**, the same curl **times out / is refused** (ufw
   proof).
5. **Contract test green (repo):** the `test_config` command above passes (regression guard).
6. **JAC end-to-end (VPS):** trigger a real generation for an application (the async loop from
   [[generation-async-loop]]). Expect: it completes, the CV/letter reflect the **7B** model's
   quality (not the old 1B), and — with `LLM_STRENGTH=standard` — the run reports the **standard**
   rung, not light. Confirm the VPS RAM stays healthy (`free -h`) throughout, since no model runs
   there now.
7. **Personal ollama alias (optional):** in the account → LLM tab, add an `ollama` config with
   `url=http://10.10.0.2:11434/v1`. It should **save** (validator accepts it because of
   `LLM_URL_ALLOWLIST=10.10.0.0/24`). Remove the allowlist entry and confirm a new save is
   **rejected** — that's the SSRF guard doing its job.

---

## Results

_(to be filled by Lukas after building it — raw test output, what worked, what bit. Read first when
debugging follow-ups.)_
