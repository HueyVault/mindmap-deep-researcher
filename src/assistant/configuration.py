import os
from dataclasses import dataclass, fields
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from dataclasses import dataclass

from enum import Enum

class SearchAPI(Enum):
    PERPLEXITY = "perplexity"
    TAVILY = "tavily"

@dataclass(kw_only=True)
class Configuration:
    """The configurable fields for the research assistant."""
    max_web_research_loops: int = 6
    local_llm: str = "gemini-2.0-flash-exp"
    search_api: SearchAPI = SearchAPI.TAVILY
    
    # Neo4j 설정 추가 (기본값 제공)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "password"

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """Create a Configuration instance from a RunnableConfig."""
        configurable = (
            config["configurable"] if config and "configurable" in config else {}
        )
        
        # 환경 변수나 configurable에서 값을 가져옴
        values: dict[str, Any] = {}
        for f in fields(cls):
            if not f.init:
                continue
                
            # 먼저 configurable에서 값을 찾고, 없으면 환경 변수에서 찾음
            env_value = os.environ.get(f.name.upper())
            config_value = configurable.get(f.name)
            
            # 값이 있으면 저장
            if config_value is not None:
                values[f.name] = config_value
            elif env_value is not None:
                values[f.name] = env_value
        
        # 기본값으로 인스턴스 생성 후 values로 업데이트
        instance = cls()
        for k, v in values.items():
            setattr(instance, k, v)
            
        return instance