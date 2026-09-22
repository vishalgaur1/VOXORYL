# data/

Private runtime directory — **gitignored**.

On first run, Voxoryl bootstraps empty files here from `setup/templates/`.
Clone the repo, copy `.env.example` → `.env`, then launch; your knowledge, memory, and logs stay local.

Shared Reels live under `reels/` (index, collections, + `media/`) — never commit.
Each reel may be gated (`promote` / `quarantine` / `reject`); only promoted items enter the knowledge vault.
