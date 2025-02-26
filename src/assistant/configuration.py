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
    
    # Neo4j 설정 추가 (기본값 없이)
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """Create a Configuration instance from a RunnableConfig."""
        configurable = (
            config["configurable"] if config and "configurable" in config else {}
        )
        values: dict[str, Any] = {
            f.name: os.environ.get(f.name.upper(), configurable.get(f.name))
            for f in fields(cls)
            if f.init
        }
        return cls(**{k: v for k, v in values.items() if v})