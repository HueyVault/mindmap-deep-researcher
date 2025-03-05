import os
import json  # JSON 파일 직접 처리 (apoc.load.json 사용 안 할 때)
from neo4j import GraphDatabase
from dotenv import load_dotenv
import re  # 이 줄 추가

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
JSON_FILE_PATH = "/apps/ollama-deep-researcher/mind_map_backups/mind_map_backup_아무거나_연구해줘_20250305_052306.json"  # 실제 파일 경로로 변경


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
                
                # 명시적 노드 생성 추가
                topic_name = data.get('research_topic', '')
                topic_id = re.sub(r'\W+', '_', topic_name.lower())
                
                # 1. 먼저 주제 노드 생성
                create_topic_result = session.run(
                    """
                    CREATE (t:Topic {id: $topic_id, name: $topic_name, timestamp: datetime()})
                    RETURN t.id as id
                    """,
                    topic_id=topic_id, topic_name=topic_name
                )
                print(f"주제 노드 생성: {create_topic_result.single()['id']}")
                
                # 2. 노드 데이터 처리 및 노드 생성
                reasoning_steps = []
                concepts = []
                
                for node in data['nodes']:
                    if 'n' in node:
                        node_data = node['n']
                        node_id = node_data.get('id', '')
                        
                        # 이미 생성한 Topic 노드 제외
                        if node_id == topic_id:
                            continue
                            
                        # 노드 유형 식별
                        if node_id.startswith('step_'):
                            # ReasoningStep 노드
                            session.run(
                                """
                                CREATE (rs:ReasoningStep {
                                    id: $id, 
                                    content: $content,
                                    timestamp: $timestamp
                                })
                                """,
                                id=node_id,
                                content=node_data.get('content', ''),
                                timestamp=node_data.get('timestamp', 'datetime()')
                            )
                            reasoning_steps.append(node_id)
                            print(f"ReasoningStep 노드 생성: {node_id}")
                        else:
                            # Concept 노드
                            session.run(
                                """
                                CREATE (c:Concept {
                                    id: $id,
                                    name: $name,
                                    description: $description
                                })
                                """,
                                id=node_id,
                                name=node_data.get('name', ''),
                                description=node_data.get('description', '')
                            )
                            concepts.append(node_id)
                            print(f"Concept 노드 생성: {node_id}")
                
                print(f"총 ReasoningStep 노드: {len(reasoning_steps)}개")
                print(f"총 Concept 노드: {len(concepts)}개")
                
                # 3. 노드 통계 확인
                count_result = session.run(
                    """
                    MATCH (n) 
                    RETURN 
                      count(n) as total,
                      count(n:Topic) as topics,
                      count(n:ReasoningStep) as steps,
                      count(n:Concept) as concepts
                    """
                )
                counts = count_result.single()
                print(f"전체 노드: {counts['total']}개")
                print(f"Topic 노드: {counts['topics']}개")
                print(f"ReasoningStep 노드: {counts['steps']}개")
                print(f"Concept 노드: {counts['concepts']}개")
                
                # 4. 관계 생성
                if len(reasoning_steps) > 0:
                    # Topic -> ReasoningStep 관계 생성
                    topic_step_result = session.run(
                        """
                        MATCH (t:Topic {id: $topic_id})
                        MATCH (rs:ReasoningStep)
                        WHERE rs.id IN $step_ids
                        CREATE (t)-[r:HAS_STEP]->(rs)
                        RETURN count(r) as rel_count
                        """,
                        topic_id=topic_id, step_ids=reasoning_steps
                    )
                    print(f"Topic-ReasoningStep 관계 생성: {topic_step_result.single()['rel_count']}개")
                
                if len(concepts) > 0:
                    # Topic -> Concept 관계 생성
                    topic_concept_result = session.run(
                        """
                        MATCH (t:Topic {id: $topic_id})
                        MATCH (c:Concept)
                        WHERE c.id IN $concept_ids
                        CREATE (t)-[r:HAS_CONCEPT]->(c)
                        RETURN count(r) as rel_count
                        """,
                        topic_id=topic_id, concept_ids=concepts
                    )
                    print(f"Topic-Concept 관계 생성: {topic_concept_result.single()['rel_count']}개")
                
                # ReasoningStep -> Concept 관계 생성 (컨텐츠 기반)
                if len(reasoning_steps) > 0 and len(concepts) > 0:
                    # 각 ReasoningStep과 Concept 가져오기
                    steps_data = session.run(
                        "MATCH (rs:ReasoningStep) RETURN rs.id as id, rs.content as content"
                    ).data()
                    
                    concepts_data = session.run(
                        "MATCH (c:Concept) RETURN c.id as id, c.name as name"
                    ).data()
                    
                    # 개념이 추론 단계에 포함되면 관계 생성
                    mention_count = 0
                    for step in steps_data:
                        step_content = step.get('content', '')
                        step_id = step.get('id', '')
                        
                        if not step_content or not step_id:
                            continue
                            
                        for concept in concepts_data:
                            concept_name = concept.get('name', '')
                            concept_id = concept.get('id', '')
                            
                            if not concept_name or not concept_id:
                                continue
                                
                            if concept_name in step_content:
                                session.run(
                                    """
                                    MATCH (rs:ReasoningStep {id: $step_id})
                                    MATCH (c:Concept {id: $concept_id})
                                    CREATE (rs)-[r:MENTIONS]->(c)
                                    """,
                                    step_id=step_id, concept_id=concept_id
                                )
                                mention_count += 1
                    
                    print(f"ReasoningStep-Concept 관계 생성: {mention_count}개")
                
                # 5. 최종 그래프 통계 확인
                final_stats = session.run(
                    """
                    MATCH ()-[r]->() 
                    RETURN 
                      count(r) as total_relations,
                      count(r:HAS_STEP) as has_step,
                      count(r:HAS_CONCEPT) as has_concept,
                      count(r:MENTIONS) as mentions
                    """
                )
                rel_counts = final_stats.single()
                print(f"총 관계 수: {rel_counts['total_relations']}개")
                print(f"HAS_STEP 관계: {rel_counts['has_step']}개")
                print(f"HAS_CONCEPT 관계: {rel_counts['has_concept']}개")
                print(f"MENTIONS 관계: {rel_counts['mentions']}개")
                
                print("마인드맵 복원 완료!")
                    
        except Exception as e:
            print(f"마인드맵 백업 복원 중 오류 발생: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":

    # 마인드맵 백업 파일 복원 (새 방식)
    import_mind_map_backup(JSON_FILE_PATH)

    # 방법 1: apoc.load.json 사용 (권장)
    # import_json_with_apoc(JSON_FILE_PATH)

    # 방법 2: JSON 파일 직접 처리 (apoc.load.json 사용 안 함)
    # import_json_manually(JSON_FILE_PATH)

    driver.close()