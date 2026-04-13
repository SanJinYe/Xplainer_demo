from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from tailevents.api import create_app
from tailevents.config import Settings


class CountingLLMClient:
    def __init__(self):
        self.calls = 0

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.3,
    ) -> str:
        self.calls += 1
        return "DataProcessor coordinates API fetching and processing."


class FakeDocRetriever:
    async def retrieve(self, package: str, symbol: str):
        return None


def test_end_to_end_ingestion_rename_and_explanation_cache():
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "integration.db"
        llm_client = CountingLLMClient()
        app = create_app(
            settings=Settings(db_path=str(db_path)),
            llm_client=llm_client,
            doc_retriever=FakeDocRetriever(),
        )

        with TestClient(app) as client:
            responses = [
                client.post(
                    "/api/v1/events",
                    json={
                        "action_type": "create",
                        "file_path": "api.py",
                        "code_snapshot": "def fetch_data(url):\n    return url\n",
                        "intent": "create a reusable HTTP fetch helper",
                        "session_id": "session-e2e",
                    },
                ),
                client.post(
                    "/api/v1/events",
                    json={
                        "action_type": "create",
                        "file_path": "processor.py",
                        "code_snapshot": (
                            "class DataProcessor:\n"
                            "    def process(self, url):\n"
                            "        return url.upper()\n"
                        ),
                        "intent": "create a processor class around incoming data",
                        "session_id": "session-e2e",
                    },
                ),
                client.post(
                    "/api/v1/events",
                    json={
                        "action_type": "modify",
                        "file_path": "processor.py",
                        "code_snapshot": (
                            "from api import fetch_data\n\n"
                            "class DataProcessor:\n"
                            "    def process(self, url):\n"
                            "        data = fetch_data(url)\n"
                            "        return data.upper()\n"
                        ),
                        "intent": "wire DataProcessor to call the fetch helper",
                        "reasoning": "centralize network retrieval before processing",
                        "session_id": "session-e2e",
                    },
                ),
                client.post(
                    "/api/v1/events",
                    json={
                        "action_type": "create",
                        "file_path": "app.py",
                        "code_snapshot": (
                            "from processor import DataProcessor\n\n"
                            "def main(url):\n"
                            "    processor = DataProcessor()\n"
                            "    return processor.process(url)\n"
                        ),
                        "intent": "add a runnable entry point for the processor",
                        "session_id": "session-e2e",
                    },
                ),
                client.post(
                    "/api/v1/events",
                    json={
                        "action_type": "rename",
                        "file_path": "api.py",
                        "code_snapshot": "def fetch_api_data(url):\n    return url\n",
                        "intent": "rename fetch_data to better match its API responsibility",
                        "session_id": "session-e2e",
                    },
                ),
            ]

            assert all(response.status_code == 201 for response in responses)

            entities_response = client.get("/api/v1/entities")
            assert entities_response.status_code == 200
            entities = entities_response.json()
            by_qname = {entity["qualified_name"]: entity for entity in entities}

            assert "fetch_api_data" in by_qname
            assert "fetch_data" not in by_qname
            assert "DataProcessor" in by_qname
            assert "DataProcessor.process" in by_qname
            assert "main" in by_qname

            fetch_entity = by_qname["fetch_api_data"]
            process_entity = by_qname["DataProcessor.process"]
            processor_entity = by_qname["DataProcessor"]

            assert fetch_entity["rename_history"][0]["old_qualified_name"] == "fetch_data"
            assert fetch_entity["entity_id"].startswith("ent_")

            outgoing_response = client.get(
                f"/api/v1/relations/{process_entity['entity_id']}/outgoing"
            )
            assert outgoing_response.status_code == 200
            outgoing_relations = outgoing_response.json()
            assert any(
                relation["target"] == fetch_entity["entity_id"]
                for relation in outgoing_relations
            )

            first_explanation = client.get(
                f"/api/v1/explain/{processor_entity['entity_id']}"
            )
            assert first_explanation.status_code == 200
            first_payload = first_explanation.json()
            assert (
                first_payload["creation_intent"]
                == "create a processor class around incoming data"
            )
            assert first_payload["from_cache"] is False

            second_explanation = client.get(
                f"/api/v1/explain/{processor_entity['entity_id']}"
            )
            assert second_explanation.status_code == 200
            second_payload = second_explanation.json()
            assert second_payload["from_cache"] is True
            assert llm_client.calls == 1
