# Packages vs Releases

| Sidebar | What it is | VOXORYL |
|---|---|---|
| **Packages** | GitHub Packages registry (Docker/ghcr.io, npm, NuGet, …) | `ghcr.io/vishalgaur1/voxoryl` — CPU-friendly **API container** |
| **Releases** | Versioned downloadable assets attached to a GitHub Release | **Windows / macOS / Linux** portable zips (`*-windows.zip`, `*-macos.zip`, …) |

Native desktop (voice orb, Win32 computer-use) needs a **host install from Releases** (or clone). The container is for exploring the HTTP API in Docker — not a full desktop substitute.

```bash
docker pull ghcr.io/vishalgaur1/voxoryl:latest
docker run --rm -p 3847:3847 ghcr.io/vishalgaur1/voxoryl:latest
# → http://127.0.0.1:3847  ·  Doctor: /api/doctor
```

Published by [`.github/workflows/package-ghcr.yml`](../.github/workflows/package-ghcr.yml) on `main` / tags / Releases.
