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
JSON_FILE_PATH = "/apps/ollama-deep-researcher/mind_map_backups/mind_map_backup_고령화_사회와_자산관리_시장의_변화_20250225_064941.json"  # 실제 파일 경로로 변경


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



if __name__ == "__main__":

    # 방법 1: apoc.load.json 사용 (권장)
    import_json_with_apoc(JSON_FILE_PATH)

    # 방법 2: JSON 파일 직접 처리 (apoc.load.json 사용 안 함)
    # import_json_manually(JSON_FILE_PATH)

    driver.close()