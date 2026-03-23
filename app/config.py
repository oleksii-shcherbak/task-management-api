from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application settings
    APP_NAME: str = "Task Management API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # Database settings
    DATABASE_URL: str

    # JWT settings
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # CORS settings
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # GitHub OAuth settings
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/github/callback"

    # Redis
    REDIS_URL: str

    # Storage settings
    STORAGE_BACKEND: str = "local"  # "local" or "s3"
    UPLOAD_DIR: str = "uploads"

    # AWS S3 settings (only required when STORAGE_BACKEND=s3)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_S3_BUCKET: str = ""
    AWS_S3_REGION: str = "us-east-1"
    AWS_S3_PRESIGNED_URL_EXPIRES: int = 3600

    # Configuration for loading from .env file
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )


# Create an instance of the settings
settings = Settings()
