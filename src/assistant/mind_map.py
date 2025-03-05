from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import re
from langchain_community.graphs import Neo4jGraph
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
import os
from assistant.utils import global_request_limiter


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
        
    # 다음 메서드 추가
    def _safe_llm_invoke(self, messages):
        """요청 제한을 고려한 안전한 LLM 호출"""
        return global_request_limiter.get_llm_response(self.llm, messages)

    def create_research_node(self, research_topic: str, node_type: str, content: str, iteration: int):
        """연구 계획 또는 반성 노드 생성"""
        # 노드 생성 시간
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 노드 생성 쿼리
        query = f"""
        MATCH (topic:ResearchTopic {{name: $topic_name}})
        CREATE (n:{node_type} {{
            content: $content,
            iteration: $iteration,
            timestamp: $timestamp
        }})-[:BELONGS_TO]->(topic)
        RETURN n
        """
        
        try:
            result = self.graph.query(
                query,
                params={
                    "topic_name": research_topic,
                    "content": content,
                    "iteration": iteration,
                    "timestamp": timestamp
                }
            )
            return result
        except Exception as e:
            print(f"{node_type} 노드 생성 중 오류: {e}")
            return None
    
    def initialize_for_topic(self, research_topic: str):
        """새로운 연구 주제에 대한 Mind Map 완전 초기화"""
        # 기존 데이터 백업
        try:
            # 타임스탬프 생성
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_id = re.sub(r'\W+', '_', research_topic.lower())
            backup_filename = f"mind_map_backup_{backup_id}_{timestamp}.json"
            
            # 백업 디렉토리 확인/생성
            backup_dir = "mind_map_backups"
            os.makedirs(backup_dir, exist_ok=True)
            backup_path = os.path.join(backup_dir, backup_filename)
            
            # 현재 데이터 백업
            nodes = self.graph.query("MATCH (n) RETURN n")
            relationships = self.graph.query("MATCH ()-[r]->() RETURN r")
            
            backup_data = {
                "timestamp": timestamp,
                "research_topic": research_topic,
                "nodes": nodes,
                "relationships": relationships
            }
            
            with open(backup_path, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2, default=str)
            
            print(f"Mind Map 백업 완료: {backup_path}")
        except Exception as e:
            print(f"Mind Map 백업 중 오류: {e}")
        
        # 기존 데이터 전체 삭제 (완전 초기화)
        try:
            # 먼저 기존 그래프 프로젝션 삭제
            try:
                self.graph.query("CALL gds.graph.drop('reasoning-graph', false) YIELD graphName")
            except Exception as e:
                # GDS 라이브러리가 없거나 프로젝션이 없을 수 있으므로 무시
                print(f"그래프 프로젝션 삭제 중 오류 (무시 가능): {e}")
            
            # 기존 데이터 전체 삭제
            self.graph.query("MATCH (n) DETACH DELETE n")
            print("기존 Mind Map 데이터 모두 삭제됨")
        except Exception as e:
            print(f"데이터 삭제 중 오류: {e}")
        
        # 연구 주제 노드 생성
        topic_id = re.sub(r'\W+', '_', research_topic.lower())
        
        # MERGE 사용하여 중복 생성 방지 (CREATE 대신)
        self.graph.query(
            """
            MERGE (t:Topic {id: $topic_id})
            ON CREATE SET t.name = $topic,
                        t.timestamp = datetime()
            ON MATCH SET t.name = $topic,
                    t.timestamp = datetime()
            """,
            {
                "topic_id": topic_id,
                "topic": research_topic
            }
        )
        
        print(f"새 연구 주제에 대한 Mind Map 초기화 완료: {research_topic}")

    def _create_schema(self):
        """Neo4j 스키마 초기화 및 제약조건 생성"""
        # 노드 유형별 제약조건 생성
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:ReasoningStep) REQUIRE r.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Concept) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Evidence) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE t.id IS UNIQUE"
        ]
        
        for query in constraints:
            try:
                self.graph.query(query)
            except Exception as e:
                print(f"스키마 초기화 오류 (무시 가능): {e}")
        
        # 그래프 프로젝션은 노드가 있을 때만 생성 가능하므로 initialize_for_topic에서 처리
    
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
            result = self._safe_llm_invoke([HumanMessage(content=prompt)])

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
        # 로깅을 위한 정보 저장
        query_info = {
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "query_type": "retrieve_context",
            "query": query,
            "research_topic": research_topic
        }
        
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
        
        intent_result = self._safe_llm_invoke([HumanMessage(content=intent_prompt)])
        intent = self._parse_llm_response(intent_result.content)
        
        # 2. 목적에 맞는 Cypher 쿼리 생성
        cypher_prompt = f"""Neo4j 그래프 데이터베이스에 대한 Cypher 쿼리를 생성해주세요.

    스키마:
    - (Topic) - 연구 주제 (id, name)
    - (ReasoningStep) - 추론 단계 (id, content, timestamp)
    - (Concept) - 개념 (id, name, description)
    - (Evidence) - 증거 (id, content, source)

    관계:
    - (Topic)-[HAS_STEP]->(ReasoningStep)
    - (Topic)-[HAS_EVIDENCE]->(Evidence)
    - (ReasoningStep)-[MENTIONS]->(Concept)
    - (ReasoningStep)-[LEADS_TO]->(ReasoningStep)
    - (Concept)-[RELATES_TO]->(Concept) 또는 더 구체적인 관계 유형

    질문: {query}
    연구 주제: {research_topic}
    분석된 의도: {json.dumps(intent)}

    중요: 먼저 Topic 노드를 찾고, 그와 연결된 데이터를 조회하는 쿼리를 작성하세요.
    연구 주제는 Topic 노드의 name 속성으로 저장되어 있습니다.

    Cypher 쿼리만 반환해주세요.
    """

        cypher_result = self._safe_llm_invoke([HumanMessage(content=cypher_prompt)])
        cypher_query = self._extract_cypher_query(cypher_result.content)
        
        # 쿼리 로깅
        query_info["cypher_query"] = cypher_query
        
        # 3. 쿼리 실행 및 결과 통합
        try:
            results = self.graph.query(cypher_query)
            query_info["query_results"] = str(results)
            
            if not results:
                # 결과가 없을 경우 폴백 쿼리 시도
                fallback_query = f"""
                MATCH (t:Topic) WHERE t.name CONTAINS "{research_topic}"
                OPTIONAL MATCH (t)-[:HAS_STEP]->(s:ReasoningStep)
                OPTIONAL MATCH (s)-[:MENTIONS]->(c:Concept)
                RETURN t.name AS Topic, collect(DISTINCT s.content) AS ReasoningSteps, 
                    collect(DISTINCT c.name) AS Concepts LIMIT 5
                """
                results = self.graph.query(fallback_query)
                query_info["fallback_query"] = fallback_query
                query_info["fallback_results"] = str(results)
                
                if not results:
                    # 로깅
                    from assistant.graph import save_research_process, SummaryState
                    
                    # 임시 state 객체 생성
                    temp_state = SummaryState(
                        research_topic=research_topic,
                        running_summary="",
                        needs_external_info=False,
                        research_loop_count=0,
                        web_research_results=[],
                        search_query=""
                    )
                    
                    save_research_process(
                        temp_state,  # 딕셔너리 대신 SummaryState 객체 전달
                        "Mind Map Query",
                        f"쿼리: {query}\nCypher: {cypher_query}\n결과: 정보 없음"
                    )
                    return "관련 정보를 찾을 수 없습니다."
            
            # 4. 결과 요약 및 형식화
            summary_prompt = f"""다음 그래프 쿼리 결과를 요약해주세요:
            {results}
            
            원래 질문: {query}
            연구 주제: {research_topic}
            
            결과를 명확하게 설명하고, 발견된 관계와 정보를 강조해주세요.
            """
            
            summary_result = self._safe_llm_invoke([HumanMessage(content=summary_prompt)])
            response = summary_result.content
            
            # 로깅
            query_info["response"] = response

            from assistant.graph import save_research_process
            # SummaryState 객체 대신 클래스 생성 없이 처리할 수 있도록 함수 가져오기
            from assistant.graph import SummaryState
            
            # 임시 state 객체 생성
            temp_state = SummaryState(
                research_topic=research_topic,
                running_summary="",
                needs_external_info=False,
                research_loop_count=0,
                web_research_results=[],
                search_query=""
            )
            
            save_research_process(
                temp_state,  # 딕셔너리 대신 SummaryState 객체 전달
                "Mind Map Query",
                f"쿼리: {query}\nCypher: {cypher_query}\n결과: {str(results)[:500]}...\n응답: {response}"
            )
            
            return response
        except Exception as e:
            error_msg = f"컨텍스트 검색 중 오류: {e}"
            print(error_msg)
            
            # 오류 로깅 코드 수정
            from assistant.graph import save_research_process, SummaryState
            
            # 임시 state 객체 생성
            temp_state = SummaryState(
                research_topic=research_topic,
                running_summary="",
                needs_external_info=False,
                research_loop_count=0,
                web_research_results=[],
                search_query=""
            )
            
            save_research_process(
                temp_state,  # 딕셔너리 대신 SummaryState 객체 전달
                "Mind Map Query Error",
                f"쿼리: {query}\nCypher: {cypher_query}\n오류: {str(e)}"
            )
            
            return f"마인드맵 쿼리 중 오류가 발생했습니다: {str(e)}"

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
            result = self._safe_llm_invoke([HumanMessage(content=prompt)])
            return result.content.strip()
        except Exception as e:
            return f"요약 생성 중 오류: {str(e)}"
        

    def _extract_concepts(self, content: str) -> List[Dict]:
        """텍스트에서 핵심 개념을 추출
        
        Returns:
            List[Dict]: 추출된 개념 리스트 
            [{"id": "concept_id", "label": "개념명", "description": "설명", "confidence": 0.9}]
        """
        if not content or content.strip() == "":
            return []
            
        prompt = f"""다음 텍스트에서 핵심 개념(entities, concepts)을 최대 10개 추출해주세요:
        
    {content}

    JSON 형식으로 응답해주세요:
    {{
        "concepts": [
            {{
                "label": "개념명",
                "description": "개념에 대한 간략한 설명 (25단어 이내)",
                "confidence": 0.9  // 정확도 (0.0~1.0)
            }}
        ]
    }}
    """
        
        try:
            result = self._safe_llm_invoke([HumanMessage(content=prompt)])
            concepts_data = self._parse_llm_response(result.content)
            
            # 개념 ID 생성 및 정규화
            concepts = []
            for idx, concept in enumerate(concepts_data.get("concepts", [])):
                if "label" in concept:
                    concept_id = re.sub(r'\W+', '_', concept["label"].lower())
                    concepts.append({
                        "id": concept_id,
                        "label": concept["label"],
                        "description": concept.get("description", ""),
                        "confidence": concept.get("confidence", 0.8)
                    })
            
            return concepts
        except Exception as e:
            print(f"개념 추출 중 오류: {e}")
            return []

    def _parse_llm_response(self, response: str) -> Dict:
        """LLM 응답에서 JSON 데이터 추출"""
        try:
            # JSON 부분만 추출
            json_match = re.search(r'({[\s\S]*})', response)
            if json_match:
                json_str = json_match.group(1)
                return json.loads(json_str)
            else:
                # JSON 형식이 명확하지 않은 경우 빈 객체 반환
                return {}
        except Exception as e:
            print(f"JSON 파싱 오류: {e}")
            return {}

    def _extract_cypher_query(self, response: str) -> str:
        """LLM 응답에서 Cypher 쿼리 추출"""
        # Cypher 쿼리를 코드 블록 내에서 찾기
        query_match = re.search(r'```(?:cypher)?\s*([\s\S]*?)```', response)
        if query_match:
            return query_match.group(1).strip()
        
        # 코드 블록이 없는 경우 전체 텍스트 사용
        return response.strip()

    def query_mind_map(self, query: str) -> str:
        """마인드맵에 직접 질의"""
        if not query:
            return "질의가 비어 있습니다."
        
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
    - (Concept)-[관계유형]->(Concept) - 관계 유형: IS_PART_OF, CAUSES, CORRELATES_WITH, CONTRADICTS, SUPPORTS, IS_TYPE_OF, FOLLOWS

    사용자 질의: {query}

    위 질의에 가장 적합한 Cypher 쿼리를 생성해 주세요.
    """
        
        try:
            # Cypher 쿼리 생성
            cypher_result = self._safe_llm_invoke([HumanMessage(content=cypher_prompt)])
            cypher_query = self._extract_cypher_query(cypher_result.content)
            
            # 쿼리 실행
            try:
                results = self.graph.query(cypher_query)
                
                # 결과 요약
                if not results:
                    return "해당 질의에 대한 결과가 없습니다."
                
                summary_prompt = f"""다음 Neo4j 쿼리 결과를 사용자가 이해하기 쉽게 요약해주세요:
                
    원래 질의: {query}
    검색 결과: {results}

    5-10문장으로 요약하여 중요 정보만 제공해주세요.
    """
                
                summary_result = self._safe_llm_invoke([HumanMessage(content=summary_prompt)])
                return summary_result.content
                
            except Exception as e:
                return f"쿼리 실행 중 오류가 발생했습니다: {str(e)}"
                
        except Exception as e:
            return f"마인드맵 질의 중 오류가 발생했습니다: {str(e)}"