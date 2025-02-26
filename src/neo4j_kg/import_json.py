import os
import json  # JSON 파일 직접 처리 (apoc.load.json 사용 안 할 때)
from neo4j import GraphDatabase
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 환경 변수에서 Neo4j 연결 정보 가져오기
NEO4J_URI = os.getenv("NEO4J_URI")  # URI (예: bolt://localhost:7687)
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")  # 기본값: neo4j

# Neo4j 드라이버 생성
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

# JSON 파일 경로 (상대 경로 또는 절대 경로)
'mind_map_backup_고령화_사회와_자산관리_시장의_변화_20250225_064941'
'mind_map_backup_고령화_사회와_자산관리_시장의_변화_20250225_081123'
'mind_map_backup_고령화_사회와_자산관리_시장의_변화_20250225_081439'
'mind_map_backup_고령화_사회와_자산관리_시장의_변화_20250226_020308'
JSON_FILE_PATH = "/import/backup_json/mind_map_backup_고령화_사회와_자산관리_시장의_변화_20250226_020308.json"  # 실제 파일 경로로 변경
JSON_FILE_PATH = "/apps/ollama-deep-researcher/mind_map_backups/mind_map_backup_고령화_사회와_자산관리_시장의_변화_20250226_020308.json"  # 실제 파일 경로로 변경


def import_json_with_apoc(file_path: str):
    """
    apoc.load.json을 사용하여 JSON 파일을 Neo4j에 로드합니다.

    Args:
        file_path (str): JSON 파일 경로.
    """

    with driver.session(database=NEO4J_DATABASE) as session:  # database 인자 추가
        # 1. apoc.load.json을 사용하여 JSON 파일 로드
        query = """
        CALL apoc.load.json($filePath) YIELD value AS data
        UNWIND data AS item
        // 이후 Cypher 쿼리에서 item 변수를 사용하여 JSON 데이터 처리
        // 예시: 노드 생성
        MERGE (n:MyNode {id: item.id})  // id는 유일해야 함 (또는 다른 고유 식별자)
        SET n += item.properties  // item.properties 안의 모든 속성을 노드에 추가
        """
        # 필요한 경우, 관계 생성 등 추가 Cypher 쿼리 작성

        # 2. Cypher 쿼리 실행 및 결과 처리
        result = session.run(query, filePath=file_path)  # filePath 파라미터 전달
        for record in result:
            print(record)  # 결과 확인 (필요에 따라 처리)
            # 예시: 생성된 노드 수 카운트 등
        print("데이터 로드 완료!")


def import_json_manually(file_path: str):
    """
    JSON 파일을 직접 읽고 처리하여 Neo4j에 데이터를 로드합니다.
    (apoc.load.json을 사용하지 않는 방법 - 더 복잡하고 번거롭지만, APOC를 사용할 수 없는 경우 대안)
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)  # JSON 파일 로드

            # 데이터 구조에 따라 Cypher 쿼리 작성
            if isinstance(data, list):  # 리스트 형태인 경우
                for item in data:
                    # 각 item에 대한 Cypher 쿼리 실행
                    # 예시: 노드 생성
                    query = """
                    MERGE (n:MyNode {id: $id})  // id는 유일해야 함
                    SET n += $properties
                    """
                    session.run(query, id=item.get("id"), properties=item.get("properties"))
            elif isinstance(data, dict): # 딕셔너리 형태인 경우
                    # 예시
                    query = """
                        MERGE (n:MyNode {id: $id})
                        SET n += $properties
                    """
                    session.run(query, id=item.get("id"), properties = item.get("properties"))

        print("데이터 로드 완료!")

def import_mind_map_backup(file_path: str):
    """
    마인드맵 백업 파일을 Neo4j에 복원합니다.
    백업 파일의 특정 구조에 맞춰 임포트 작업을 수행합니다.
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)  # JSON 파일 로드
                
                print(f"백업 파일 로드: {file_path}")
                print(f"연구 주제: {data.get('research_topic', '알 수 없음')}")
                print(f"타임스탬프: {data.get('timestamp', '알 수 없음')}")
                
                # 먼저 기존 데이터를 모두 삭제
                session.run("MATCH (n) DETACH DELETE n")
                print("기존 데이터 삭제 완료")
                
                # 노드 복원
                nodes_created = 0
                if 'nodes' in data:
                    for node_data in data['nodes']:
                        if 'n' in node_data:
                            node_props = node_data['n']
                            
                            # 노드 레이블 결정 (id 패턴이나 특정 속성으로 판단)
                            label = "Node"  # 기본 레이블
                            if 'id' in node_props:
                                if node_props['id'].startswith('step_'):
                                    label = "ReasoningStep"
                                elif 'topic' in node_props:
                                    label = "ReasoningStep"
                                elif node_props.get('name') == data.get('research_topic'):
                                    label = "Topic"
                                else:
                                    label = "Concept"
                            
                            # 노드 생성 쿼리
                            props_str = ", ".join([f"{k}: ${k}" for k in node_props.keys()])
                            query = f"""
                            CREATE (n:{label} {{{props_str}}})
                            RETURN n
                            """
                            
                            try:
                                session.run(query, **node_props)
                                nodes_created += 1
                            except Exception as e:
                                print(f"노드 생성 중 오류: {e} - 데이터: {node_props}")
                
                print(f"노드 {nodes_created}개 생성 완료")
                
                # 관계 복원 - 좀 더 복잡하므로 두 단계로 처리
                # 1. 모든 노드 ID와 관계 유형을 추출
                # 2. ID를 기반으로 관계 생성
                
                # 관계 복원을 위해 노드 매핑
                relationships_query = """
                MATCH (source), (target)
                WHERE source.id = $source_id AND target.id = $target_id
                CREATE (source)-[r:$rel_type]->(target)
                SET r.timestamp = datetime()
                RETURN source, r, target
                """
                
                # 임시 대안: 기본 관계 생성 (ReasoningStep -> Topic, Concept -> Concept 등)
                topic_id = None
                for node in data['nodes']:
                    if 'n' in node and node['n'].get('id', '').startswith('고령화_사회'):
                        topic_id = node['n']['id']
                        break
                
                if topic_id:
                    # Topic -> ReasoningStep 관계 생성
                    step_query = """
                    MATCH (t:Topic), (s:ReasoningStep)
                    WHERE t.id = $topic_id
                    CREATE (t)-[r:HAS_STEP]->(s)
                    RETURN count(r) as rel_count
                    """
                    result = session.run(step_query, topic_id=topic_id)
                    print(f"Topic-ReasoningStep 관계 생성: {result.single()['rel_count']}개")
                    
                    # ReasoningStep -> Concept 관계 생성
                    concept_query = """
                    MATCH (s:ReasoningStep), (c:Concept)
                    WHERE NOT s.id IS NULL
                    CREATE (s)-[r:MENTIONS]->(c)
                    RETURN count(r) as rel_count
                    """
                    result = session.run(concept_query)
                    print(f"ReasoningStep-Concept 관계 생성: {result.single()['rel_count']}개")
                    
                    # 시간순 ReasoningStep 간 관계 생성
                    timeline_query = """
                    MATCH (s1:ReasoningStep), (s2:ReasoningStep)
                    WHERE s1.timestamp < s2.timestamp
                    WITH s1, s2, s1.timestamp as t1, s2.timestamp as t2
                    ORDER BY t1, t2
                    WITH collect(s1) as steps
                    UNWIND range(0, size(steps)-2) as i
                    WITH steps[i] as current, steps[i+1] as next
                    CREATE (current)-[r:LEADS_TO]->(next)
                    RETURN count(r) as rel_count
                    """
                    result = session.run(timeline_query)
                    print(f"순차적 ReasoningStep 관계 생성: {result.single()['rel_count']}개")
                
                print("마인드맵 복원 완료!")
                    
        except Exception as e:
            print(f"마인드맵 백업 복원 중 오류 발생: {e}")

if __name__ == "__main__":

    # 마인드맵 백업 파일 복원 (새 방식)
    import_mind_map_backup(JSON_FILE_PATH)

    # 방법 1: apoc.load.json 사용 (권장)
    # import_json_with_apoc(JSON_FILE_PATH)

    # 방법 2: JSON 파일 직접 처리 (apoc.load.json 사용 안 함)
    # import_json_manually(JSON_FILE_PATH)

    driver.close()