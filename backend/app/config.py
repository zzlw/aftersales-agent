"""全局配置：环境变量驱动，多 Provider 可切换（OpenAI 兼容协议）。"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM（OpenAI 兼容端点：DeepSeek / Azure / Ollama 均可）
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"        # 生成主模型
    llm_model_fast: str = "deepseek-chat"   # 路由/评估用小快模型

    # Embedding（硅基流动 bge-m3，OpenAI 兼容）
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

    database_url: str = "postgresql://agent:agent@localhost:5433/aftersales"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
