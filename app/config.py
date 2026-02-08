from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # Application settings
    APP_NAME: str = "Task Management API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # Database settings
    DATABASE_URL: str

    # Security settings
    SECRET_KEY: str = ""

    # Configuration for loading from .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


# Create an instance of the settings
settings = Settings()