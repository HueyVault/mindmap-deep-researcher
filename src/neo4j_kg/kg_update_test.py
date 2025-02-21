from langchain.chains import GraphCypherQAChain
from langchain_community.graphs import Neo4jGraph
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from typing import Dict, List
from datetime import datetime
from dotenv import load_dotenv
import os
import json  # json.loads를 위한 import도 추가
from langchain_core.messages import SystemMessage  # 추가된 import
from langchain_core.messages import HumanMessage  # SystemMessage 대신 HumanMessage 사용

# .env 파일 로드
load_dotenv()

# 환경 변수에서 Neo4j 연결 정보 가져오기
NEO4J_URL = os.getenv("NEO4J_URL")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)
import time
from functools import wraps

def rate_limit(delay=5):  # 2초에서 5초로 증가
    """Simple rate limiting decorator"""
    def decorator(func):
        last_called = [0]
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_called[0]
            if elapsed < delay:
                time.sleep(delay - elapsed)
            result = func(*args, **kwargs)
            last_called[0] = time.time()
            return result
        return wrapper
    return decorator

class Neo4jKnowledgeGraphManager:
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

    def create_constraints(self):
        """노드 ID에 대한 유니크 제약조건 생성"""
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Concept) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Evidence) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Conclusion) REQUIRE n.id IS UNIQUE"
        ]
        
        for constraint in constraints:
            self.graph.query(constraint)
            
    def upload_node(self, node_type: str, node_id: str, properties: Dict):
        """노드 생성 또는 업데이트"""
        properties['last_updated'] = datetime.now().isoformat()
        
        cypher = f"""
        MERGE (n:{node_type} {{id: $node_id}})
        SET n += $properties
        RETURN n
        """
        
        self.graph.query(
            cypher,
            {"node_id": node_id, "properties": properties}
        )
        
    def upload_relation(self, 
                       source_type: str,
                       source_id: str, 
                       target_type: str,
                       target_id: str, 
                       relation_type: str,
                       properties: Dict = None):
        """관계 생성 또는 업데이트"""
        if properties is None:
            properties = {}
        properties['last_updated'] = datetime.now().isoformat()
        
        cypher = f"""
        MATCH (source:{source_type} {{id: $source_id}})
        MATCH (target:{target_type} {{id: $target_id}})
        MERGE (source)-[r:{relation_type}]->(target)
        SET r += $properties
        RETURN r
        """
        
        self.graph.query(
            cypher,
            {
                "source_id": source_id,
                "target_id": target_id,
                "properties": properties
            }
        )

    def upload_knowledge_graph(self, nodes: List[Dict], relations: List[Dict]):
        """전체 지식 그래프 업로드"""
        # 1. 제약조건 생성
        self.create_constraints()
        
        # 2. 노드 업로드
        for node in nodes:
            self.upload_node(
                node_type=node["type"],
                node_id=node["id"],
                properties={
                    "label": node["label"],
                    **node.get("properties", {})
                }
            )
            
        # 3. 관계 업로드
        for relation in relations:
            self.upload_relation(
                source_type=relation["source_type"],
                source_id=relation["source_id"],
                target_type=relation["target_type"],
                target_id=relation["target_id"],
                relation_type=relation["relation_type"],
                properties=relation.get("properties", {})
            )

    def propagate_relationships(self, new_node_id: str, similarity_threshold: float = 0.7):
        """새로운 노드 추가 시 전체 그래프와의 관계를 동적으로 분석하고 업데이트"""
        
        # 1. 새로운 노드 정보 가져오기
        new_node_query = """
        MATCH (n {id: $node_id})
        RETURN n.label as label, n.description as description
        """
        new_node = self.graph.query(new_node_query, {"node_id": new_node_id})[0]

        # 2. 기존 모든 노드 가져오기
        existing_nodes_query = """
        MATCH (n)
        WHERE n.id <> $node_id
        RETURN n.id as id, n.label as label, n.description as description
        """
        existing_nodes = self.graph.query(existing_nodes_query, {"node_id": new_node_id})

        # 3. 관계 분석 및 업데이트
        for existing_node in existing_nodes:
            # LLM을 사용하여 두 노드 간의 관계 분석
            relationship_analysis = self._analyze_relationship(
                node1={
                    "label": new_node["label"],
                    "description": new_node["description"]
                },
                node2={
                    "label": existing_node["label"],
                    "description": existing_node["description"]
                }
            )

            # 임계값을 넘는 관계만 생성
            if relationship_analysis["similarity"] >= similarity_threshold:
                # 양방향 관계 생성
                self._create_bidirectional_relationship(
                    node1_id=new_node_id,
                    node2_id=existing_node["id"],
                    relationship=relationship_analysis
                )

    @rate_limit(delay=5)  # 5초 간격으로 증가
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=20),  # 대기 시간도 증가
    )
    def _analyze_relationship(self, node1: Dict, node2: Dict) -> Dict:
        """LLM을 사용하여 두 노드 간의 관계를 분석"""
        prompt = f"""
        다음 두 개념 간의 관계를 분석하고 정확히 JSON 형식으로만 응답해주세요:

        개념 1:
        - 레이블: {node1['label']}
        - 설명: {node1['description']}

        개념 2:
        - 레이블: {node2['label']}
        - 설명: {node2['description']}

        응답 형식:
        {{
            "relationship_type": "INCLUDES",  # 영문 대문자와 언더스코어만 사용 (예: IS_PART_OF, CONTAINS, RELATED_TO)
            "similarity": 0.8,  # 반드시 0과 1 사이의 숫자로 입력
            "direction": "BIDIRECTIONAL/FORWARD/BACKWARD",
            "explanation": "관계에 대한 설명"
        }}

        주의사항:
        - relationship_type은 영문 대문자와 언더스코어만 사용
        - similarity는 반드시 숫자여야 합니다
        - 다른 설명이나 추가 텍스트 없이 오직 JSON 형식으로만 응답하세요.
        """

        result = self.llm.invoke([
            HumanMessage(content=prompt)
        ])
        
        # 코드 블록 마커 제거
        content = result.content
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        relationship_analysis = json.loads(content)
        
        # 필수 키 확인
        required_keys = ["relationship_type", "similarity", "direction", "explanation"]
        if not all(key in relationship_analysis for key in required_keys):
            raise ValueError(f"Missing required keys in response. Got: {relationship_analysis}")
        
        # similarity가 문자열인 경우 float로 변환
        if isinstance(relationship_analysis["similarity"], str):
            relationship_analysis["similarity"] = float(relationship_analysis["similarity"])
        
        # relationship_type을 Neo4j 호환 형식으로 변환
        relationship_analysis["relationship_type"] = (
            relationship_analysis["relationship_type"]
            .upper()
            .replace(" ", "_")
            .replace("-", "_")
        )
            
        return relationship_analysis
    
    def _create_bidirectional_relationship(self, node1_id: str, node2_id: str, relationship: Dict):
        """분석된 관계를 바탕으로 양방향 또는 단방향 관계 생성"""
        
        properties = {
            "similarity": relationship["similarity"],
            "explanation": relationship["explanation"],
            "last_updated": datetime.now().isoformat()
        }

        if relationship["direction"] in ["BIDIRECTIONAL", "FORWARD"]:
            self.upload_relation(
                source_type="Concept",
                source_id=node1_id,
                target_type="Concept",
                target_id=node2_id,
                relation_type=relationship["relationship_type"],
                properties=properties
            )

        if relationship["direction"] in ["BIDIRECTIONAL", "BACKWARD"]:
            self.upload_relation(
                source_type="Concept",
                source_id=node2_id,
                target_type="Concept",
                target_id=node1_id,
                relation_type=relationship["relationship_type"],
                properties=properties
            )

    def update_graph_relationships(self, similarity_threshold: float = 0.7, batch_size: int = 3):  # 배치 크기 감소
        """전체 그래프의 관계를 재분석하고 업데이트 (배치 처리)"""
        nodes = self.graph.query("""
        MATCH (n)
        RETURN n.id as id, n.label as label, n.description as description
        """)

        self.graph.query("MATCH ()-[r]->() DELETE r")

        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes), batch_size):
                batch_end = min(j + batch_size, len(nodes))
                print(f"Processing batch: nodes {i} with nodes {j}-{batch_end}")
                
                for k in range(j, batch_end):
                    relationship_analysis = self._analyze_relationship(
                        node1={"label": nodes[i]["label"], "description": nodes[i]["description"]},
                        node2={"label": nodes[k]["label"], "description": nodes[k]["description"]}
                    )

                    if relationship_analysis["similarity"] >= similarity_threshold:
                        self._create_bidirectional_relationship(
                            node1_id=nodes[i]["id"],
                            node2_id=nodes[k]["id"],
                            relationship=relationship_analysis
                        )
                
                time.sleep(5)  

# 사용 예시:
if __name__ == "__main__":
    kg_manager = Neo4jKnowledgeGraphManager(NEO4J_URL, NEO4J_USERNAME, NEO4J_PASSWORD)

    # 새로운 노드 추가 시 전체 그래프와의 관계 자동 분석
    kg_manager.upload_node(
        node_type="Concept",
        node_id="deep_learning",
        properties={
            "label": "딥러닝",
            "description": "여러 층의 인공신경망을 사용하여 데이터로부터 복잡한 패턴을 학습하는 기계학습의 한 분야"
        }
    )
    kg_manager.propagate_relationships("deep_learning")

    # 전체 그래프 관계 재분석
    kg_manager.update_graph_relationships()