import os
import yaml
from pydantic import BaseModel
from typing import List

class CookieConfig(BaseModel):
    name: str
    cookie: str

class AppConfig(BaseModel):
    base_api_url: str
    cookies: List[CookieConfig]

def load_config(config_path: str = "config.yaml") -> AppConfig:
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件 {config_path} 不存在")
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return AppConfig(**data)

config = load_config()
