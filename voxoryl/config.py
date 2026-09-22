from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b"
    # Dual-model routing: tiny model for greetings / snappy chat; main for real work.
    # VOXORYL_MAIN_MODEL overrides OLLAMA_MODEL when set; otherwise OLLAMA_MODEL is main.
    voxoryl_main_model: str = ""
    voxoryl_fast_model: str = "qwen2.5:0.5b"
    # Local (Ollama) vs Cloud (free streaming APIs). Widget can override → data/inference.json
    voxoryl_inference_mode: str = "local"  # local | cloud
    voxoryl_cloud_provider: str = "groq"  # groq | gemini | openrouter | nvidia
    voxoryl_cloud_api_key: str = ""  # shared override; else provider-specific keys below
    voxoryl_cloud_model: str = ""  # empty → provider default
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_api_key: str = ""
    google_api_key: str = ""
    openrouter_api_key: str = ""
    nvidia_api_key: str = ""
    voxoryl_data_dir: Path = Path("./data")
    voxoryl_port: int = 3847
    voxoryl_host: str = "127.0.0.1"

    # Connectors (optional — tools degrade gracefully when empty)
    obsidian_vault: Path | None = None
    github_token: str = ""
    github_user: str = ""
    email_imap_host: str = ""
    email_imap_user: str = ""
    email_imap_password: str = ""
    email_imap_folder: str = "INBOX"
    voxoryl_workspace: Path = Path("./workspace_sandbox")
    comfyui_url: str = "http://127.0.0.1:8188"
    tts_voice: str = "en-US-AndrewMultilingualNeural"

    # Screen see + mouse/keyboard control (OFF by default — enable in .env)
    computer_use_enabled: bool = False
    ollama_vision_model: str = "qwen2.5vl:3b"
    computer_use_max_steps: int = 12
    computer_use_pause: float = 0.12
    screen_watch_enabled: bool = True
    screen_watch_interval_sec: int = 45

    # Embeddings + search powerups
    ollama_embed_model: str = "nomic-embed-text"
    searxng_url: str = ""

    # Speech-to-text (accurate mic). Parakeet = Hex-style engine; whisper = fallback/Hindi
    asr_backend: str = "auto"  # auto | parakeet | whisper
    asr_model: str = "nemo-parakeet-tdt-0.6b-v3"
    whisper_model: str = "small"

    # Optional SMTP for approved outreach sends
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # Vercel — auto-host generated sites after approval
    vercel_token: str = ""
    vercel_team_id: str = ""

    # Instagram — VOXORYL's own Business/Creator account (Meta Graph API)
    instagram_access_token: str = ""
    instagram_business_account_id: str = ""
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_graph_version: str = "v21.0"

    # Owner / Chrome profile matching (optional — never hardcode personal names in source)
    voxoryl_owner_name: str = ""
    voxoryl_owner_email: str = ""
    voxoryl_chrome_profile_match: str = ""
    voxoryl_chrome_profile_prefer: str = "photo"  # photo | orange | any

    @property
    def owner_name(self) -> str:
        return (self.voxoryl_owner_name or "").strip()

    @property
    def owner_email(self) -> str:
        return (self.voxoryl_owner_email or "").strip()

    @property
    def chrome_profile_match_phrases(self) -> list[str]:
        raw = (self.voxoryl_chrome_profile_match or "").strip()
        phrases = [p.strip().lower() for p in raw.split(",") if p.strip()]
        name = self.owner_name.lower()
        if name and name not in phrases:
            phrases.insert(0, name)
        if name:
            first = name.split()[0]
            if first and first not in phrases:
                phrases.append(first)
        for extra in ("my profile", "photo profile", "my chrome profile", "chrome profile"):
            if extra not in phrases:
                phrases.append(extra)
        return phrases

    @property
    def main_model(self) -> str:
        """Primary brain (planning, council, knowledge, tools)."""
        return (self.voxoryl_main_model or self.ollama_model or "qwen3.5:4b").strip()

    @property
    def fast_model(self) -> str:
        """Tiny model for greetings / short chitchat (no tools)."""
        return (self.voxoryl_fast_model or "qwen2.5:0.5b").strip()

    @property
    def memory_path(self) -> Path:
        return self.voxoryl_data_dir / "memory.json"

    @property
    def skills_path(self) -> Path:
        return self.voxoryl_data_dir / "skills.json"

    @property
    def council_log_path(self) -> Path:
        return self.voxoryl_data_dir / "council_log.jsonl"

    @property
    def daily_path(self) -> Path:
        return self.voxoryl_data_dir / "daily.json"

    @property
    def knowledge_path(self) -> Path:
        return self.voxoryl_data_dir / "knowledge.md"

    @property
    def knowledge_graph_path(self) -> Path:
        return self.voxoryl_data_dir / "knowledge_graph.json"

    @property
    def mindmap_html_path(self) -> Path:
        return self.voxoryl_data_dir / "mindmap.html"


settings = Settings()
