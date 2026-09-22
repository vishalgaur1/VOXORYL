# Security Policy

## Reporting a vulnerability

Email **vishalgaur2002@gmail.com** with a clear description and steps to reproduce.
Please do not open a public GitHub issue for unpatched credential or RCE-class bugs.

## Secrets & private data

- **Never commit** `.env`, API keys, tokens, cookies, browser profiles, or anything under `data/`.
- Copy `.env.example` → `.env` and fill in your own keys.
- `data/` holds memory, chat, knowledge, logs, and DBs — it is gitignored on purpose.
- Icons and product branding under `voxoryl/static/` are fine to keep public.

## If a key was ever exposed

1. **Rotate it immediately** at the provider (Groq, Gemini, NVIDIA, OpenRouter, GitHub, etc.).
2. Treat the old key as compromised even if you later remove it from the tree.
3. Do not rewrite git history unless you know you need to; rotation is the important fix.

## Commercial contact

Licensing / commercial inquiries: **vishalgaur2002@gmail.com** (see [`LICENSE`](LICENSE) and [`COMMERCIAL_LICENSE.md`](COMMERCIAL_LICENSE.md)).
