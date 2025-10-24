import json
import uuid
import os
from dotenv import load_dotenv
from datetime import datetime
from typing import Dict, Any, List, Optional, Literal
from supabase import create_client


class SolarEnvelopeBuilder:
    @staticmethod
    def create_task(project_id: str, from_agent: str, to_agent: str, task_text: str) -> dict:
        return{
            "schema": "company.a2a.v1",
            "type": "task.create",
            "routing": {
            "project_id": project_id,
            "from": f"agent: {from_agent}",
            "to": f"agent:{to_agent}",
            "reply_to": None
            },
            "payload": {"text": task_text},
            "status": "sent",
            "timestamps": {
                "created": datetime.now().isoformat(),
                "updated": datetime.now().isoformat()
            },
            "id": str(uuid.uuid4())
        }
    
    @staticmethod
    def create_task_update(project_id: str, from_agent: str, progress_text: str, status: str = "processing", reply_to: str = None)-> dict:
        return{
            "schema" :"company.a2a.v1",
            "type": "task.update",
            "routing": {
                "project_id": project_id,
                "from": f"agent:{from_agent}",
                "to": "broadcast",
                "reply_to": reply_to
            },
            "payload": {"text": progress_text},
            "status": status,
            "timestamps": {
                "created": datetime.now().isoformat(),
                "updated": datetime.now().isoformat()
            },
            "id": str(uuid.uuid4())
        }
    

class SolarDatabaseAdapter:
    def __init__(self, supabase_client):
        self.supabase = supabase_client

    def send_envelope(self, envelope: dict, human_id: str):
        from_agent = envelope["routing"]["from"].replace("agent:", "")
        to_agent = envelope["routing"]["to"].replace("agent:", "")

        db_data = {
            "id": envelope["id"],
            "project_id": envelope["routing"]["project_id"],
            "human_id": human_id,
            "agent_name": from_agent,
            "to_agent": to_agent if to_agent != "broadcast" else "broadcast",
            "intent": envelope["payload"]["text"],
            "status": envelope["status"],
            "content": envelope,
            "reply_to": envelope["routing"]["reply_to"]
        }

        response = self.supabase.table('messages').insert(db_data).execute()
        return response.data[0] if response.data else None
    

"""def test_envelope():
    load_dotenv()
    supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY"))
    adapter = SolarDatabaseAdapter(supabase)

    auth_resp = supabase.auth.sign_in_with_password({"email": "test@user.com", "password": "test11"})

    envelope = SolarEnvelopeBuilder.create_task("test_project", "coordinator", "worker", "Test task")
    sent = adapter.send_envelope(envelope, auth_resp.user.id)
    print(f"Envelope sent: {sent['id']}")


if __name__ == "__main__":
    test_envelope()"""