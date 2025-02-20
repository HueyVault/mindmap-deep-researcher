from langchain.chains import GraphCypherQAChain
from langchain_community.graphs import Neo4jGraph
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from typing import Dict, List
from datetime import datetime
from dotenv import load_dotenv
import os
# ... 기존 CYPHER_GENERATION_TEMPLATE, CYPHER_QA_TEMPLATE 코드 ...

class Neo4jKnowledgeGraphManager:
    def __init__(self, url: str, username: str, password: str):
        self.graph = Neo4jGraph(
            url=url, 
            username=username, 
            password=password
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
    
    def update_node_properties(self, node_type: str, node_id: str, properties: Dict):
        """노드 속성 업데이트"""
        properties['last_updated'] = datetime.now().isoformat()
        
        cypher = f"""
        MATCH (n:{node_type} {{id: $node_id}})
        SET n += $properties
        RETURN n
        """
        
        self.graph.query(
            cypher,
            {"node_id": node_id, "properties": properties}
        )
    
    def delete_node(self, node_type: str, node_id: str):
        """노드 삭제 (연결된 관계도 자동 삭제)"""
        cypher = f"""
        MATCH (n:{node_type} {{id: $node_id}})
        DETACH DELETE n
        """
        
        self.graph.query(cypher, {"node_id": node_id})

# .env 파일 로드
load_dotenv()

# 환경 변수에서 Neo4j 연결 정보 가져오기
NEO4J_URL = os.getenv("NEO4J_URL")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# 사용 예시:
if __name__ == "__main__":
    
    # 매니저 인스턴스 생성
    kg_manager = Neo4jKnowledgeGraphManager(
        url=NEO4J_URL,
        username=NEO4J_USERNAME,
        password=NEO4J_PASSWORD
    )
    
    # 예시 데이터
    sample_nodes = [
        {
            "id": "concept1",
            "type": "Concept",
            "label": "인공지능",
            "properties": {"description": "기계학습과 딥러닝을 포함하는 분야"}
        },
        {
            "id": "concept2",
            "type": "Concept",
            "label": "머신러닝",
            "properties": {"description": "데이터로부터 학습하는 알고리즘"}
        }
    ]
    
    sample_relations = [
        {
            "source_type": "Concept",
            "source_id": "concept1",
            "target_type": "Concept",
            "target_id": "concept2",
            "relation_type": "INCLUDES",
            "properties": {"confidence": 0.9}
        }
    ]
    
    # 지식 그래프 업로드
    kg_manager.upload_knowledge_graph(sample_nodes, sample_relations)