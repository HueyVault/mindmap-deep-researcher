from typing import Dict, List, Optional, Tuple
import networkx as nx
from dataclasses import dataclass
from langchain_google_genai import ChatGoogleGenerativeAI
import os
import json
import time
from functools import wraps
from datetime import datetime

def rate_limit(delay=3):  # 딜레이를 3초로 증가
    """Rate limiting decorator"""
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

@dataclass
class Entity:
    name: str
    type: str
    attributes: Dict[str, str]

@dataclass
class Relationship:
    source: str
    target: str
    type: str

class MindMapAgent:
    def __init__(self, save_dir: str = "research_results/mind_maps"):
        self.graph = nx.DiGraph()
        self.save_dir = save_dir
        self.session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(save_dir, exist_ok=True)
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash-exp",
            temperature=0,
            max_retries=3,
            retry_delay=3,
        )
    
    def save_graph(self, research_topic: str) -> Tuple[str, str]:
        """그래프를 세션별로 저장"""
        # 세션 타임스탬프를 사용한 파일명 생성
        filename = f"{research_topic.replace(' ', '_')[:30]}_{self.session_timestamp}_mind_map"
        
        # 그래프 복사본 생성
        graph_for_save = self.graph.copy()
        
        # 속성을 문자열로 변환
        for node in graph_for_save.nodes():
            attrs = graph_for_save.nodes[node]
            if 'attributes' in attrs:
                attrs['attributes'] = json.dumps(attrs['attributes'], ensure_ascii=False)
        
        # GraphML 형식으로 저장
        graphml_path = os.path.join(self.save_dir, f"{filename}.graphml")
        nx.write_graphml(graph_for_save, graphml_path)
        
        # JSON 형식으로 저장
        json_data = {
            "metadata": {
                "research_topic": research_topic,
                "session_timestamp": self.session_timestamp,
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "node_count": len(self.graph.nodes),
                "edge_count": len(self.graph.edges)
            },
            "nodes": [
                {
                    "name": node,
                    "type": self.graph.nodes[node].get("type", ""),
                    "attributes": self.graph.nodes[node].get("attributes", {})
                }
                for node in self.graph.nodes
            ],
            "edges": [
                {
                    "source": u,
                    "target": v,
                    "type": self.graph.edges[u, v].get("type", "")
                }
                for u, v in self.graph.edges
            ]
        }
        
        json_path = os.path.join(self.save_dir, f"{filename}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
            
        return graphml_path, json_path

    def load_graph(self, filepath: str):
        """저장된 그래프 로드"""
        if filepath.endswith('.graphml'):
            self.graph = nx.read_graphml(filepath)
            # 문자열로 저장된 속성을 다시 딕셔너리로 변환
            for node in self.graph.nodes():
                attrs = self.graph.nodes[node]
                if 'attributes' in attrs:
                    try:
                        attrs['attributes'] = json.loads(attrs['attributes'])
                    except json.JSONDecodeError:
                        attrs['attributes'] = {}
        elif filepath.endswith('.json'):
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.graph = nx.DiGraph()
            for node in data["nodes"]:
                self.graph.add_node(
                    node["name"],
                    type=node["type"],
                    attributes=node["attributes"]
                )
            for edge in data["edges"]:
                self.graph.add_edge(
                    edge["source"],
                    edge["target"],
                    type=edge["type"]
                )

    @rate_limit(delay=3)
    def extract_entities(self, reasoning_chain: str) -> List[Entity]:
        """추론 체인에서 엔티티 추출"""
        prompt = """다음 추론 체인에서 주요 엔티티들을 추출하세요.
        각 엔티티에 대해 이름, 유형, 속성을 식별하세요.
        
        추론 체인:
        {reasoning_chain}
        
        JSON 형식으로만 응답하세요:
        {{
            "entities": [
                {{
                    "name": "엔티티명",
                    "type": "엔티티 유형",
                    "attributes": {{
                        "속성1": "값1",
                        "속성2": "값2"
                    }}
                }}
            ]
        }}"""
        
        try:
            response = self.llm.invoke(prompt.format(reasoning_chain=reasoning_chain))
            content = response.content
            
            # JSON 형식 추출 (마크다운 코드 블록 제거)
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
                
            data = json.loads(content)
            
            entities = []
            for entity_data in data.get("entities", []):
                entity = Entity(
                    name=entity_data.get("name", ""),
                    type=entity_data.get("type", ""),
                    attributes=entity_data.get("attributes", {})
                )
                entities.append(entity)
                
            return entities
        except Exception as e:
            print(f"Entity extraction error: {e}")
            print(f"LLM response: {response.content}")
            return []
        
    @rate_limit(delay=3)
    def identify_relationships(self, entities: List[Entity]) -> List[Relationship]:
        """엔티티 간의 관계 식별"""
        if not entities:
            return []
            
        # 엔티티 목록을 문자열로 변환
        entities_str = "\n".join([
            f"- {entity.name} (유형: {entity.type})" 
            for entity in entities
        ])
        
        prompt = """다음 엔티티들 간의 의미적 관계를 식별하세요:
        
        엔티티들:
        {entities}
        
        JSON 형식으로만 응답하세요:
        {{
            "relationships": [
                {{
                    "source": "시작 엔티티",
                    "target": "도착 엔티티",
                    "type": "관계 유형"
                }}
            ]
        }}"""
        
        try:
            response = self.llm.invoke(prompt.format(entities=entities_str))
            content = response.content
            
            # JSON 형식 추출
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
                
            data = json.loads(content)
            
            relationships = []
            for rel_data in data.get("relationships", []):
                rel = Relationship(
                    source=rel_data.get("source", ""),
                    target=rel_data.get("target", ""),
                    type=rel_data.get("type", "")
                )
                relationships.append(rel)
                
            return relationships
        except Exception as e:
            print(f"Relationship identification error: {e}")
            print(f"LLM response: {response.content}")
            return []

    def update_graph(self, reasoning_chain: str):
        """추론 체인을 기반으로 그래프 업데이트"""
        try:
            entities = self.extract_entities(reasoning_chain)
            if entities:
                relationships = self.identify_relationships(entities)
                
                # 그래프에 엔티티 추가
                for entity in entities:
                    self.graph.add_node(entity.name, type=entity.type, attributes=entity.attributes)
                    
                # 그래프에 관계 추가
                for rel in relationships:
                    if rel.source and rel.target:  # 빈 값이 아닌 경우에만 추가
                        self.graph.add_edge(rel.source, rel.target, type=rel.type)
        except Exception as e:
            print(f"Graph update error: {e}")
    
    @rate_limit(delay=3)
    def query_mind_map(self, query: str) -> str:
        """마인드맵에 질의하여 관련 정보 검색"""
        # 커뮤니티 클러스터링 수행
        communities = nx.community.louvain_communities(self.graph.to_undirected())
        
        # 질의와 가장 관련있는 커뮤니티 찾기
        relevant_nodes = []
        for community in communities:
            community_nodes = list(community)
            # 관련성 점수 계산 로직 구현 필요
            
        # 관련 정보 요약
        summary_prompt = f"""다음 정보를 바탕으로 질문에 답변하세요:
        
        질문: {query}
        
        관련 정보:
        {relevant_nodes}
        
        간단명료하게 답변해주세요."""
        
        response = self.llm.invoke(summary_prompt)
        return response.content