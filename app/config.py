from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # API Settings
    api_key: str = "your-secret-api-key-change-in-production"
    max_clip_duration_seconds: int = 600  # 10 minutes
    
    # Database
    database_url: str = "postgresql+asyncpg://clipper:clipper@postgres:5432/clipper"
    
    # RabbitMQ
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    rabbitmq_exchange: str = "clips"
    rabbitmq_queue: str = "clip_jobs"
    rabbitmq_dlq: str = "clip_jobs_dlq"
    max_retry_attempts: int = 2
    
    # MinIO/S3
    s3_endpoint_url: str = "http://minio:9000"  # Internal endpoint for uploads
    s3_public_endpoint_url: str = "http://localhost:9000"  # Public endpoint for presigned URLs
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "clips"
    s3_region: str = "us-east-1"
    
    # Worker Settings
    worker_concurrency: int = 2
    temp_download_dir: str = "/tmp/downloads"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
