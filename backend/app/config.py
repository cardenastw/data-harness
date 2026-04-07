from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ollama_base_url: str = "http://localhost:11434/v1"
    model_name: str = "qwen2.5:3b"
    database_path: str = "demo/coffee_shop.db"
    sql_query_timeout: float = 30.0
    sql_max_rows: int = 500
    max_tool_iterations: int = 10
    contexts_dir: str = "app/contexts"

    model_config = {"env_prefix": "", "case_sensitive": False}


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
