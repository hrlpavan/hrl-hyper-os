# HRL Hyper OS

A hybrid operating system combining an immutable Linux kernel base ([`torvalds/linux`](https://github.com/torvalds/linux) + [`configuration.nix`](configuration.nix)), macOS Aqua/Sonoma desktop UX ([`shell.css`](shell.css) + live HTML5 window manager), and a Feudal Hierarchical Reinforcement Learning ([`hrld.py`](hrld.py)) OS daemon.

## Upstream Architecture & Credits

- **Linux Kernel (`kernel/linux`)**: Upstream kernel tree tracked from [`torvalds/linux`](https://github.com/torvalds/linux) via [`.gitmodules`](.gitmodules).
- **Immutable System Layer**: Declarative NixOS + Hyprland + Waybar configuration in [`configuration.nix`](configuration.nix).
- **Feudal HRL Engine**: Manager $\to$ Worker tabular Q-learning daemon in [`hrld.py`](hrld.py).

## Quick Start (macOS / Linux / Windows Live Desktop)

```bash
python3 hrld.py
```

Opens `http://127.0.0.1:8765` with:
- **macOS Top Menu Bar & Translucent Dock**
- **⌘K Spotlight** connected to the Feudal HRL Manager (`manager_select` -> `worker_exec`)
- **Draggable & Resizable Windows** (POSIX/Windows Terminal, Finder File Browser, Persistent Subgoal Q-Table)
- **Live RL Feedback**: Train subgoal routing weights with `👍 / 👎` persisted to `q_table.json` / SQLite

## Self-Check

```bash
python3 hrld.py --check
```

## Build Bootable Linux ISO (NixOS)

```bash
nixos-generate -f iso -c configuration.nix
```

## Contributors

- **Pavan HRL** ([@hrlpavan](https://github.com/hrlpavan)) — Creator & Lead Architect
- **devsum1614-source** ([@devsum1614-source](https://github.com/devsum1614-source)) — Cross-Platform Windows & Unix Support, SQLite Persistence, and CI Workflow
