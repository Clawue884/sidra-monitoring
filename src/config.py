"""Configuration management for DevOps Agent."""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ollama Configuration
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # SSH Defaults
    ssh_user: str = "root"
    ssh_password: str = "123456"
    ssh_alt_user: str = "sidra"
    ssh_alt_password: str = "Wsxk_8765"
    ssh_timeout: int = 30
    ssh_key_path: Optional[str] = None

    # Networks to scan
    scan_networks: str = "192.168.71.0/24,192.168.92.0/24,192.168.91.0/24"

    # Discovery settings
    discovery_threads: int = 10
    discovery_timeout: int = 5

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8200

    # Paths
    output_dir: Path = Path("./output")
    reports_dir: Path = Path("./output/reports")
    db_path: Path = Path("./data/devops_agent.db")

    # Monitoring
    monitor_interval: int = 60
    alert_webhook_url: Optional[str] = None

    # Logging
    log_level: str = "INFO"
    log_file: Optional[Path] = Path("./logs/devops_agent.log")

    @property
    def networks_list(self) -> list[str]:
        """Get list of networks to scan."""
        return [n.strip() for n in self.scan_networks.split(",")]

    def ensure_dirs(self):
        """Create necessary directories."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
