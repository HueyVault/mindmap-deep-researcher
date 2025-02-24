
from typing import Dict, List, Optional
from datetime import datetime
from langchain_community.graphs import Neo4jGraph
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
import json
from dataclasses import dataclass
from enum import Enum

class MindMapTokenType(Enum):
    QUERY = "MIND_MAP_QUERY"
    UPDATE = "MIND_MAP_UPDATE"

@dataclass
class MindMapToken:
    type: MindMapTokenType
    query: str
    context: str = ""



class MindMapAgent:
    """연구 과정의 추론 맥락을 저장하고 구조화하는 Mind Map 에이전트"""
    
    def __init__(self, url: str, username: str, password: str):
        self.graph = Neo4jGraph(
            url=url,
            username=username,
            password=password
        )
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash-exp",
            temperature=0
        )
        self._create_constraints()

    def process_token(self, token: MindMapToken) -> str:
        """Mind Map 토큰 처리"""
        if token.type == MindMapTokenType.QUERY:
            return self.query_mind_map(token.query)
        elif token.type == MindMapTokenType.UPDATE:
            return self.add_reasoning_step("", token.context)
        return ""

    def cluster_and_summarize(self) -> List[Dict]:
        """추론 컨텍스트를 클러스터링하고 요약"""
        # 1. 커뮤니티 클러스터링 수행 (Louvain 알고리즘 사용)
        clusters = self.graph.query("""
            CALL gds.louvain.stream('reasoning-graph')
            YIELD nodeId, communityId
            WITH communityId, collect(nodeId) AS nodeIds
            WHERE size(nodeIds) > 1  // 최소 2개 이상의 노드가 있는 커뮤니티만 필터링 (선택 사항)
            RETURN communityId, nodeIds
        """)


        
        summaries = []
        for cluster in clusters:
            # 2. 각 클러스터의 노드 내용 수집
            nodes_content = self.graph.query(f"""
            MATCH (n)
            WHERE id(n) IN {cluster['nodeIds']}
            RETURN collect(n.content) as contents
            """)
            
            # 3. LLM을 사용하여 클러스터 요약 생성
            if nodes_content and nodes_content[0]['contents']:
                summary = self._generate_cluster_summary(nodes_content[0]['contents'])
                summaries.append({
                    "cluster_id": cluster['communityId'],
                    "summary": summary
                })
            else:
                summaries.append({
                    "cluster_id": cluster['communityId'],
                    "summary": "No content to summarize for this cluster."
                })
            
        return summaries


    def _generate_cluster_summary(self, contents: List[str]) -> str:
        """LLM을 사용하여 클러스터 내용 요약"""
        prompt = f"""다음 관련 내용들을 간단히 요약해주세요:

내용:
{' '.join(contents)}

핵심 주제와 주요 포인트를 포함하여 2-3문장으로 요약해주세요."""

        result = self.llm.invoke([HumanMessage(content=prompt)])
        return result.content.strip()

    def _create_constraints(self):
        """노드 유니크 제약조건 생성"""
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Concept) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:ReasoningStep) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Evidence) REQUIRE n.id IS UNIQUE"
        ]
        for constraint in constraints:
            self.graph.query(constraint)
            
    def add_reasoning_step(self, research_topic: str, reasoning_content: str, step_type: str) -> str:
        """추론 단계를 Mind Map에 추가"""
        step_id = f"step_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # 1. 추론 단계 노드 생성
        self.graph.query(
            """
            MERGE (s:ReasoningStep {id: $step_id})
            SET s.content = $content,
                s.timestamp = datetime(),
                s.topic = $topic,
                s.type = $type
            """,
            {
                "step_id": step_id,
                "content": reasoning_content,
                "topic": research_topic,
                "type": step_type
            }
        )

        # 2. 이전 단계와 연결
        self.graph.query(
            """
            MATCH (s:ReasoningStep {id: $step_id})
            MATCH (prev:ReasoningStep)
            WHERE prev.timestamp < s.timestamp
            WITH s, prev
            ORDER BY prev.timestamp DESC
            LIMIT 1
            MERGE (prev)-[r:LEADS_TO]->(s)
            """,
            {"step_id": step_id}
        )

        # 3. 개념 추출 및 관계 분석
        concepts = self._extract_concepts(reasoning_content)
        
        # 4. 개념 노드 생성 및 관계 설정
        for concept in concepts:
            self.graph.query(
                """
                MERGE (c:Concept {id: $id})
                SET c.label = $label,
                    c.description = $description
                WITH c
                MATCH (s:ReasoningStep {id: $step_id})
                MERGE (s)-[r:INCLUDES]->(c)
                SET r.confidence = $confidence
                """,
                {
                    "id": concept["id"],
                    "label": concept["label"],
                    "description": concept["description"],
                    "step_id": step_id,
                    "confidence": concept.get("confidence", 0.8)
                }
            )

        return step_id
    
    def _extract_concepts(self, content: str) -> List[Dict]:
        """추론 내용에서 주요 개념 추출"""
        prompt = f"""다음 추론 내용에서 주요 개념들을 추출하고 관계를 분석해주세요.
        
        추론 내용:
        {content}
        
        다음 JSON 형식으로 응답해주세요:
        {{
            "concepts": [
                {{
                    "id": "unique_concept_id",
                    "label": "개념명",
                    "description": "개념 설명",
                    "confidence": 0.9  // 0과 1 사이의 신뢰도
                }}
            ]
        }}
        """
        
        result = self.llm.invoke([HumanMessage(content=prompt)])
        concepts = self._parse_llm_response(result.content)
        return concepts.get("concepts", [])
    
    def query_mind_map(self, query: str) -> str:
        """Mind Map 질의 개선"""
        # 1. 질의 의도 분석
        intent_prompt = f"""다음 질문의 의도를 분석해주세요:
        {query}
        
        다음 형식으로 응답:
        {{
            "intent": "temporal|logical|conceptual|relational",
            "focus": ["관심 개념들"],
            "temporal_range": "all|recent|specific"
        }}
        """
        
        intent_result = self.llm.invoke([HumanMessage(content=intent_prompt)])
        intent = self._parse_llm_response(intent_result.content)
        
        # 2. 의도에 따른 쿼리 생성
        if intent["intent"] == "temporal":
            cypher_query = self._generate_temporal_query(intent)
        elif intent["intent"] == "logical":
            cypher_query = self._generate_logical_query(intent)
        else:
            cypher_query = self._generate_basic_query(intent)
            
        # 3. 쿼리 실행 및 결과 포맷팅
        results = self.graph.query(cypher_query)
        return self._format_query_results(results)
    
    def _parse_llm_response(self, content: str) -> Dict:
        """LLM 응답에서 JSON 추출"""
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content.strip())
    
    def _extract_cypher_query(self, content: str) -> str:
        """LLM 응답에서 Cypher 쿼리 추출"""
        if "```cypher" in content:
            content = content.split("```cypher")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return content.strip()
    
    def _format_query_results(self, results: List[Dict]) -> str:
        """쿼리 결과를 읽기 쉬운 형식으로 변환"""
        prompt = f"""다음 데이터베이스 쿼리 결과를 자연스러운 문장으로 변환해주세요:
        {results}
        """
        
        response = self.llm.invoke([HumanMessage(content=prompt)])
        return response.content.strip()