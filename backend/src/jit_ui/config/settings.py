"""Configuration is loaded from the project .env and environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_prefix="JIT_", extra="ignore")

    data_dir: Path = PROJECT_ROOT / ".data"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_rpm: int = 12
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3-vl:2b-instruct"
    ollama_context: int = 4096
    ollama_max_output_tokens: int = 192
    ollama_image_max_width: int = 960
    testbed_url: str = "http://127.0.0.1:3000"
    testbed_api_url: str = "http://127.0.0.1:8001"
    browser_headless: bool = True
    browser_close_timeout_seconds: float = 5
    run_timeout_seconds: int = 600
    max_model_calls: int = 20
    max_cold_actions: int = 30
    max_repair_calls: int = 3
    max_repair_actions: int = 6
    max_repaired_steps: int = 2
    queue_capacity: int = 10
    embedding_backend: str = "lexical"
    similarity_threshold: float = 0.65
    locator_timeout_ms: int = 800
    condition_timeout_ms: int = 5000
    chromium_path: str | None = None
    fixture_username: str = "demo"
    fixture_password: str = "demo-password"

    @property
    def database_url(self) -> str:
        return "sqlite:///" + (self.data_dir / "jit_ui.sqlite3").as_posix()
