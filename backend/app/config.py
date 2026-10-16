"""Settings loaded from environment / backend/.env."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
load_dotenv(BASE_DIR / ".env")


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.base_dir = BASE_DIR
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./data/dhakapath.db")
        self.jwt_secret = os.getenv("JWT_SECRET", "change_me")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.access_token_expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
        self.cors_origins = [
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
            if o.strip()
        ]
        self.demo_secret = os.getenv("DEMO_SECRET", "change_me_debug_secret")
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.groq_api_key = os.getenv("GROQ_API_KEY", "")
        self.llm_provider = os.getenv("LLM_PROVIDER", "openrouter")
        # models are overridable; the defaults are free-tier models and may need updating
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        self.groq_model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        self.llm_timeout_s = float(os.getenv("LLM_TIMEOUT_S", "8"))
        self.chat_llm_rewrite = _bool(os.getenv("CHAT_LLM_REWRITE", "true"))
        self.chat_demo_simulation = _bool(os.getenv("CHAT_ALLOW_DEMO_SIMULATION", "true"))
        self.llm_cache_enabled = _bool(os.getenv("LLM_CACHE_ENABLED", "true"))
        self.upload_dir = os.getenv("UPLOAD_DIR", "./data/uploads")
        self.max_upload_images = int(os.getenv("MAX_UPLOAD_IMAGES", "5"))

    @property
    def coordination_dir(self) -> Path:
        return BASE_DIR / "co-ordination"

    @property
    def generated_dir(self) -> Path:
        return BASE_DIR / "data" / "generated"


settings = Settings()
