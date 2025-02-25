from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import re
from langchain_community.graphs import Neo4jGraph
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

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
        self._create_schema()
        
    def initialize_for_topic(self, research_topic: str):
        """새로운 연구 주제에 대한 Mind Map 초기화
        
        Args:
            research_topic (str): 새 연구 주제
        """
        # 1. 기존에 같은 주제의 노드가 있다면 모두 삭제
        topic_id = re.sub(r'\W+', '_', research_topic.lower())
        
        # 2. 연구 주제 노드 생성
        self.graph.query(
            """
            MERGE (t:Topic {id: $topic_id})
            SET t.name = $topic,
                t.timestamp = datetime()
            """,
            {
                "topic_id": topic_id,
                "topic": research_topic
            }
        )
        
        # 3. 그래프 프로젝션 준비 (최소 1개의 노드가 필요)
        # 위에서 생성한 Topic 노드가 있으므로 이제 프로젝션 시도 가능
        try:
            self.graph.query("""
            CALL gds.graph.project.cypher(
                'reasoning-graph',
                'MATCH (n) RETURN id(n) AS id',
                'MATCH (a)-[r]->(b) RETURN id(a) AS source, id(b) AS target, type(r) AS type',
                {validateRelationships: false}
            )
            """)
        except Exception as e:
            print(f"그래프 프로젝션 초기화 중 오류 (새 연구 시작 시 정상): {e}")

    def _create_schema(self):
        """Neo4j 스키마 초기화 및 제약조건 생성"""
        # 노드 유형별 제약조건 생성
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:ReasoningStep) REQUIRE r.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Concept) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Evidence) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE t.id IS UNIQUE",
            
            # 그래프 알고리즘을 위한 그래프 프로젝션 생성
            """
            CALL gds.graph.project.cypher(
                'reasoning-graph',
                'MATCH (n) RETURN id(n) AS id',
                'MATCH (a)-[r]->(b) RETURN id(a) AS source, id(b) AS target, type(r) AS type',
                {validateRelationships: false}
            )
            """
        ]
        
        for query in constraints:
            try:
                self.graph.query(query)
            except Exception as e:
                print(f"스키마 초기화 오류 (무시 가능): {e}")
    
    def auto_update(self, state: Any, step_name: str) -> None:
        """모든 상태 변화를 자동으로 Mind Map에 업데이트"""
        # 상태 객체에서 중요 정보 추출
        research_topic = getattr(state, 'research_topic', None)
        running_summary = getattr(state, 'running_summary', None)
        web_research_results = getattr(state, 'web_research_results', [])
        
        # 연구 주제 노드 생성 또는 업데이트
        if research_topic:
            self.graph.query(
                """
                MERGE (t:Topic {id: $topic_id})
                SET t.name = $topic,
                    t.timestamp = datetime()
                """,
                {
                    "topic_id": re.sub(r'\W+', '_', research_topic.lower()),
                    "topic": research_topic
                }
            )
        
        # 추론 단계 추가
        if running_summary:
            content = running_summary
            if isinstance(content, list):
                content = "\n".join(content) if all(isinstance(x, str) for x in content) else str(content)
            step_id = self.add_reasoning_step(research_topic, content, step_name)
            
            # 개념 추출 및 관계 분석
            self._extract_and_link_concepts(content, step_id)
        
        # 웹 검색 결과 저장
        for result in web_research_results:
            if isinstance(result, dict) and 'content' in result:
                evidence_id = f"evidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                
                # 증거 노드 생성
                self.graph.query(
                    """
                    MERGE (e:Evidence {id: $evidence_id})
                    SET e.content = $content,
                        e.source = $source,
                        e.timestamp = datetime()
                    """,
                    {
                        "evidence_id": evidence_id,
                        "content": result.get('content', ''),
                        "source": result.get('source', '')
                    }
                )
                
                # 연구 주제와 연결
                if research_topic:
                    topic_id = re.sub(r'\W+', '_', research_topic.lower())
                    self.graph.query(
                        """
                        MATCH (t:Topic {id: $topic_id})
                        MATCH (e:Evidence {id: $evidence_id})
                        MERGE (t)-[r:HAS_EVIDENCE]->(e)
                        """,
                        {
                            "topic_id": topic_id,
                            "evidence_id": evidence_id
                        }
                    )
    
    def add_reasoning_step(self, research_topic: str, reasoning_content: str, step_type: str) -> str:
        """추론 단계를 Mind Map에 추가"""
        step_id = f"step_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # 추론 단계 노드 생성
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

        # 연구 주제와 연결
        if research_topic:
            topic_id = re.sub(r'\W+', '_', research_topic.lower())
            self.graph.query(
                """
                MATCH (t:Topic {id: $topic_id})
                MATCH (s:ReasoningStep {id: $step_id})
                MERGE (t)-[r:HAS_STEP]->(s)
                """,
                {
                    "topic_id": topic_id,
                    "step_id": step_id
                }
            )

        # 이전 단계와 시간순 연결
        self.graph.query(
            """
            MATCH (s:ReasoningStep {id: $step_id})
            MATCH (prev:ReasoningStep)
            WHERE prev.timestamp < s.timestamp AND prev.topic = $topic
            WITH s, prev
            ORDER BY prev.timestamp DESC
            LIMIT 1
            MERGE (prev)-[r:LEADS_TO]->(s)
            """,
            {"step_id": step_id, "topic": research_topic}
        )

        return step_id

    def _extract_and_link_concepts(self, content: str, step_id: str) -> None:
        """텍스트에서 개념 추출 및 관계 분석"""
        concepts = self._extract_concepts(content)
        
        # 개념 노드 생성 및 단계와 연결
        for concept in concepts:
            # 개념 노드 생성
            self.graph.query(
                """
                MERGE (c:Concept {id: $concept_id})
                SET c.name = $name,
                    c.description = $description
                """,
                {
                    "concept_id": concept["id"],
                    "name": concept["label"],
                    "description": concept["description"]
                }
            )
            
            # 개념과 추론 단계 연결
            self.graph.query(
                """
                MATCH (s:ReasoningStep {id: $step_id})
                MATCH (c:Concept {id: $concept_id})
                MERGE (s)-[r:MENTIONS]->(c)
                SET r.confidence = $confidence
                """,
                {
                    "step_id": step_id,
                    "concept_id": concept["id"],
                    "confidence": concept.get("confidence", 0.8)
                }
            )
        
        # 개념 간 관계 분석 및 생성
        self._analyze_concept_relationships(concepts, content)
    
    def _analyze_concept_relationships(self, concepts: List[Dict], content: str) -> None:
        """개념 간 관계 분석 및 그래프에 반영"""
        if len(concepts) < 2:
            return
        
        concept_ids = [c["id"] for c in concepts]
        concept_names = [c["label"] for c in concepts]
        
        prompt = f"""분석: 다음 텍스트에서 언급된 개념들 간의 관계를 분석해주세요.

텍스트:
{content}

개념들:
{', '.join(concept_names)}

각 개념 쌍 간의 관계를 JSON 형식으로 반환해주세요:
{{
    "relationships": [
        {{
            "source": "개념1_ID",
            "target": "개념2_ID",
            "relation": "관계 유형",
            "description": "관계 설명",
            "confidence": 0.9
        }}
    ]
}}

관계 유형은 다음 중 하나여야 합니다: IS_PART_OF, CAUSES, CORRELATES_WITH, CONTRADICTS, SUPPORTS, IS_TYPE_OF, FOLLOWS
"""
        
        try:
            result = self.llm.invoke([HumanMessage(content=prompt)])
            relationships = self._parse_llm_response(result.content)
            
            # 관계 생성
            for rel in relationships.get("relationships", []):
                if "source" in rel and "target" in rel:
                    self.graph.query(
                        f"""
                        MATCH (source:Concept {{id: $source_id}})
                        MATCH (target:Concept {{id: $target_id}})
                        MERGE (source)-[r:{rel['relation']}]->(target)
                        SET r.description = $description,
                            r.confidence = $confidence
                        """,
                        {
                            "source_id": rel["source"],
                            "target_id": rel["target"],
                            "description": rel.get("description", ""),
                            "confidence": rel.get("confidence", 0.7)
                        }
                    )
        except Exception as e:
            print(f"관계 분석 중 오류: {e}")
    
    def retrieve_context(self, query: str, research_topic: str) -> str:
        """추론에 필요한 관련 컨텍스트 검색"""
        # 1. 질의 의도 분석
        intent_prompt = f"""다음 질문의 의도를 분석해주세요:
        {query}
        
        다음 형식으로 응답:
        {{
            "intent": "temporal|logical|conceptual|relational",
            "focus_concepts": ["관심 개념들"],
            "temporal_constraint": "all|recent|specific"
        }}
        """
        
        intent_result = self.llm.invoke([HumanMessage(content=intent_prompt)])
        intent = self._parse_llm_response(intent_result.content)
        
        # 2. 목적에 맞는 Cypher 쿼리 생성
        cypher_prompt = f"""Neo4j 그래프 데이터베이스에 대한 Cypher 쿼리를 생성해주세요.

스키마:
- (Topic) - 연구 주제
- (ReasoningStep) - 추론 단계
- (Concept) - 개념 
- (Evidence) - 증거

관계:
- (Topic)-[HAS_STEP]->(ReasoningStep)
- (Topic)-[HAS_EVIDENCE]->(Evidence)
- (ReasoningStep)-[MENTIONS]->(Concept)
- (ReasoningStep)-[LEADS_TO]->(ReasoningStep)
- (Concept)-[RELATES_TO]->(Concept) 또는 더 구체적인 관계 유형

질문: {query}
연구 주제: {research_topic}
분석된 의도: {json.dumps(intent)}

Cypher 쿼리만 반환해주세요.
"""

        cypher_result = self.llm.invoke([HumanMessage(content=cypher_prompt)])
        cypher_query = self._extract_cypher_query(cypher_result.content)
        
        # 3. 쿼리 실행 및 결과 통합
        try:
            results = self.graph.query(cypher_query)
            if not results:
                return "관련 정보를 찾을 수 없습니다."
            
            # 4. 결과 요약 및 형식화
            summary_prompt = f"""다음 그래프 쿼리 결과를 요약해주세요:
            {results}
            
            원래 질문: {query}
            """
            
            summary_result = self.llm.invoke([HumanMessage(content=summary_prompt)])
            return summary_result.content
        except Exception as e:
            print(f"컨텍스트 검색 중 오류: {e}")
            return f"오류가 발생했습니다: {str(e)}"

    def cluster_and_summarize(self) -> List[Dict]:
        """추론 컨텍스트를 클러스터링하고 요약"""
        try:
            # 1. 그래프 프로젝션 확인/생성
            try:
                self.graph.query("CALL gds.graph.exists('reasoning-graph')")
            except:
                self.graph.query("""
                CALL gds.graph.project.cypher(
                    'reasoning-graph',
                    'MATCH (n:Concept) RETURN id(n) AS id',
                    'MATCH (a:Concept)-[r]->(b:Concept) RETURN id(a) AS source, id(b) AS target',
                    {validateRelationships: false}
                )
                """)
            
            # 2. 커뮤니티 클러스터링 수행
            clusters = self.graph.query("""
            CALL gds.louvain.stream('reasoning-graph')
            YIELD nodeId, communityId
            WITH communityId, collect(nodeId) AS nodeIds
            WHERE size(nodeIds) > 1
            RETURN communityId, nodeIds
            """)
            
            if not clusters:
                return [{"error": "클러스터가 발견되지 않음"}]
            
            summaries = []
            for cluster in clusters:
                # 3. 클러스터에 포함된 개념과 관련 추론 단계 검색
                cluster_data = self.graph.query(f"""
                MATCH (c:Concept)
                WHERE id(c) IN {cluster['nodeIds']}
                WITH collect(c.name) AS concepts
                
                MATCH (s:ReasoningStep)-[r:MENTIONS]->(c:Concept)
                WHERE id(c) IN {cluster['nodeIds']}
                RETURN concepts, collect(DISTINCT s.content) AS reasoning_steps
                """)
                
                if cluster_data and len(cluster_data) > 0:
                    # 4. 클러스터 요약 생성
                    cluster_info = cluster_data[0]
                    summary = self._generate_cluster_summary(
                        concepts=cluster_info.get('concepts', []),
                        reasoning_steps=cluster_info.get('reasoning_steps', [])
                    )
                    
                    summaries.append({
                        "cluster_id": cluster['communityId'],
                        "concepts": cluster_info.get('concepts', []),
                        "summary": summary
                    })
            
            return summaries
            
        except Exception as e:
            print(f"클러스터링 중 오류: {e}")
            return [{"error": str(e)}]

    def _generate_cluster_summary(self, concepts: List[str], reasoning_steps: List[str]) -> str:
        """LLM을 사용하여 클러스터 요약 생성"""
        prompt = f"""다음 개념들과 관련 추론 단계를 요약해주세요.

개념들: {', '.join(concepts)}

추론 과정:
{' '.join(reasoning_steps[:3])}  # 처음 몇 개만 사용

이 개념 그룹의 핵심 주제와 관계를 3-4문장으로 요약해주세요.
"""

        try:
            result = self.llm.invoke([HumanMessage(content=prompt)])
            return result.content.strip()
        except Exception as e:
            return f"요약 생성 중 오류: {str(e)}"