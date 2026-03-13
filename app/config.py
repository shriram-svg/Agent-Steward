from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""

    # Ecosystem service URLs (internal Docker network)
    task_manager_url: str = "http://task-manager:8000"
    state_db_url: str = "http://state-db:8003"
    hiloop_url: str = "http://hiloop-gateway:8006"
    stimulus_bus_url: str = "http://stimulus-bus:8010"

    # Postgres DSN for direct DB access
    postgres_dsn: str = "postgresql://postgres:postgres@postgres:5432"

    # All known ecosystem services for health checks
    ecosystem_services: dict = {
        "brain": "http://brain:8000",
        "task-manager": "http://task-manager:8000",
        "state-db": "http://state-db:8003",
        "hiloop-gateway": "http://hiloop-gateway:8006",
        "manifest": "http://manifest:8000",
        "provisioner": "http://provisioner:8002",
        "forge": "http://forge:8001",
        "observer": "http://observer:8007",
        "janitor": "http://janitor:8008",
        "model-router": "http://model-router:8004",
        "artifact-store": "http://artifact-store:8000",
        "secret-manager": "http://secret-manager:8001",
        "stimulus-bus": "http://stimulus-bus:8010",
        "deployment-worker": "http://deployment-worker:8009",
    }

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
