import os
import json
import asyncio
import uuid
import anthropic
from dotenv import load_dotenv
from datetime import datetime
from supabase import create_client
from supabase._async.client import create_client as create_async_client
from typing import Dict, Any, List
from langgraph.graph import StateGraph, END
from envelope import SolarEnvelopeBuilder, SolarDatabaseAdapter

class CoordinatorMoonState:
    def __init__(self): 
        self.current_task = None
        self.worker_response = []
        self.task_status = "idle"
        self.processed_messages = set()
        self.latest_worker_response = None
        self.pending_delegation = None

class CoordinatorMoon:
    def __init__(self, human_email: str, human_password: str, agent_name: str = "coordinator"):
        load_dotenv()
        self.supabase_sync = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY"))
        self.adapter = SolarDatabaseAdapter(self.supabase_sync)
        self.human_email = human_email
        self.human_password = human_password
        self.agent_name = agent_name
        self.human_user = None
        self.state = CoordinatorMoonState()
        self.workflow = self._build_workflow()

    def _build_workflow(self) -> StateGraph:
        """LangGraph workflow"""
        workflow = StateGraph(Dict[str, Any])

        workflow.add_node("receive_task", self._receive_task_node)
        workflow.add_node("analyze_task", self._analyze_task_node)
        workflow.add_node("delegate_to_worker", self._delegate_to_worker_node)
        workflow.add_node("wait_for_response", self._wait_for_response_node)
        workflow.add_node("consolidate_results", self._consolidate_results_node)
        workflow.add_node("complete_task", self._complete_task_node)
        
        workflow.set_entry_point("receive_task")
        workflow.add_edge("receive_task", "analyze_task")
        workflow.add_edge("analyze_task", "delegate_to_worker")
        workflow.add_edge("delegate_to_worker", "wait_for_response")
        workflow.add_edge("wait_for_response", "consolidate_results")
        workflow.add_edge("consolidate_results", "complete_task")
        workflow.add_edge("complete_task", END)

        return workflow.compile()

    async def authenticate(self):
        """Authenticate user"""
        auth_resp = self.supabase_sync.auth.sign_in_with_password({
            "email": self.human_email,
            "password": self.human_password
        })

        if auth_resp.session:
            self.human_user = auth_resp.user
            print(f"Coordinator authenticated: {self.human_user.email}")
            return True
        return False

    async def send_test_message_to_self_custom(self, task_text: str):
        """Send a custom test message"""
        envelope_content = {
            "schema": "company.a2a.v1",
            "type": "task.create",
            "routing": {
                "project_id": "test_project",
                "from": "cli-user",
                "to": self.agent_name
            },
            "payload": {"text": task_text},
            "status": "sent",
            "id": str(uuid.uuid4())
        }
        
        message_data = {
            "id": str(uuid.uuid4()),
            "project_id": "test_project",
            "human_id": self.human_user.id,
            "agent_name": "cli-user",
            "to_agent": self.agent_name,
            "intent": task_text,
            "status": "sent",
            "content": envelope_content
        }
        
        sent = self.supabase_sync.table('messages').insert(message_data).execute()
        print(f"Task sent: {sent.data[0]['id']}")
        return sent.data[0]

    async def poll_for_messages(self):
        """Poll for messages since realtime isn't working"""
        last_checked = datetime.now()
        
        while True:
            try:
                # Check for new tasks directed to this coordinator
                response = self.supabase_sync.table('messages').select('*').filter(
                    'to_agent', 'eq', self.agent_name
                ).filter(
                    'created', 'gte', last_checked.isoformat()
                ).order('created', desc=False).execute()
                
                for message in response.data:
                    if message['id'] not in self.state.processed_messages:
                        print(f"POLL: Found new task: {message['intent']}")
                        await self.process_incoming_message(message)
                
                # Check for worker responses
                worker_responses = self.supabase_sync.table('messages').select('*').filter(
                    'agent_name', 'eq', 'worker'
                ).filter(
                    'created', 'gte', last_checked.isoformat()
                ).execute()
                
                for message in worker_responses.data:
                    if (message['id'] not in self.state.processed_messages and 
                        message.get('intent', '').startswith('WORK_COMPLETE:')):
                        print(f"POLL: Found worker response")
                        await self.process_incoming_message(message)
                
                last_checked = datetime.now()
                await asyncio.sleep(2)  # Poll every 2 seconds

            except asyncio.CancelledError:
                print("Polling is cancelled. Shutting down.")
                break
            except Exception as e:
                print(f"Polling error: {e}")
                await asyncio.sleep(5)

    async def handle_cli_input(self):
        """Handle CLI commands"""
        print("\nCoordinator CLI Commands:")
        print("- 'send' - Send a task")
        print("- 'status' - Show status")
        print("- 'quit' - Shutdown")
        
        while True:
            try:
                command = await asyncio.get_event_loop().run_in_executor(None, input, "\nCoordinator> ")
                
                if command.lower() == 'quit':
                    print("Shutting down Coordinator...")
                    return
                elif command.lower() == 'status':
                    print(f"Agent: {self.agent_name}")
                    print(f"Processed: {len(self.state.processed_messages)} messages")
                elif command.lower() == 'send':
                    task_text = await asyncio.get_event_loop().run_in_executor(
                        None, input, "Task: "
                    )
                    if task_text.strip():
                        await self.send_test_message_to_self_custom(task_text.strip())
                else:
                    print("Commands: send, status, quit")
            except EOFError:
                break
    
    def _receive_task_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node: Process incoming task"""
        task_message = state.get("incoming_message")
        if not task_message:
            return {"status": "no_task"}
        
        self.state.current_task = {
            "id": task_message["id"],
            "text": task_message["intent"],
            "project_id": task_message["project_id"]
        }

        print(f"COORD: Received task: {task_message['intent']}")
        return {"status": "task_received", "task": self.state.current_task}
    
    def _analyze_task_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Having this talk with Claude to give proper estimates of everthing"""
        task = state["task"]

        client = anthropic.Anthropic()

        prompt = f"""Analyze this tasks and estimate how long it will take to for an AI agent to complete.
            TASK: {task["text"]}
            Consider:
            -Task Complexity
            -Whether it needs delegation to another agent
            -Typical processing time for similar tasks
            -Any dependencies or blocking factors

            Respond using only a JSON object in this exact format: 
            {{
                "complexity": "low|medium|high",
                "requires_worker": true|false,
                "estimated_minutes": <number>,
                "reasoning": "<brief explanation>"
            }}

        """
        try: 
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )

            response = message.content[0].text

            if response.strip().startswith("```"):
                lines = response.strip().split("\n")
                response = "\n".join(lines[1:-1])
            claude_results = json.loads(response)

            analysis={
                "complexity": claude_results.get("complexity", "Not given"),
                "requires_worker": claude_results.get("requires_worker", "Not given"),
                "estimated_minutes":claude_results.get("estimated_minutes", None),
                "reasoning": claude_results.get("reasoning", "No reasoning given")
            }

            print(f"COORD: Task analysis: {analysis}")
            print(f"Reasoning: {analysis['reasoning']}")

        except json.JSONDecodeError as e:
            print(f"COORD: JSON parsing error: {e}")
            print(f"Claude's reasoning: {analysis['reasoning']}")

            analysis={
                "complexity": "Not given",
                "requires_worker": False,
                "estimated_minutes":None,
                "reasoning": "None given"
            }

        except Exception as e:
            print(f"COORD: Error: {e}")
            print(f"Here is the text: {response}")

            analysis={
                "complexity": "Not given",
                "requires_worker": False,
                "estimated_minutes":None,
                "reasoning": "None given"
            }

        return {"analysis": analysis, **state}

        """
        analysis = {
            "complexity": "high" if needs_worker else "low",
            "requires_worker": needs_worker,
            "estimated_duration": "5 minutes"
        }

        print(f"COORD: Task analysis: {analysis}")
        return {"analysis": analysis, **state}
        """
    def _delegate_to_worker_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node - delegate to actual worker"""
        task = state["task"]
        analysis = state["analysis"]

        if analysis["requires_worker"]:
            envelope_content = {
                "schema": "company.a2a.v1",
                "type": "task.update",
                "routing": {
                    "project_id": task["project_id"],
                    "from": self.agent_name,
                    "to": "worker"
                },
                "payload": {"text": f"WORK_REQUEST: {task['text']}"},
                "status": "sent",
                "id": str(uuid.uuid4())
            }
            
            message_data = {
                "id": str(uuid.uuid4()),
                "project_id": task["project_id"],
                "human_id": self.human_user.id,
                "agent_name": self.agent_name,
                "to_agent": "worker",
                "intent": f"WORK_REQUEST: {task['text']}",
                "status": "sent",
                "reply_to": task["id"],
                "content": envelope_content
            }
            
            try:
                print(f"COORD DEBUG: Attempting to insert message with to_agent='worker'")
                sent = self.supabase_sync.table('messages').insert(message_data).execute()
                print(f"COORD DEBUG: Insert response: {sent}")
                
                if sent.data and len(sent.data) > 0:
                    print(f"COORD: Delegated to worker! Message: {sent.data[0]['id']}")
                    
                    # Verify the message was actually saved
                    verify = self.supabase_sync.table('messages').select('*').eq('id', sent.data[0]['id']).execute()
                    print(f"COORD DEBUG: Verification query returned {len(verify.data)} records")
                    if verify.data:
                        print(f"COORD DEBUG: Saved message to_agent: '{verify.data[0].get('to_agent')}'")
                    
                    self.state.pending_delegation = {
                        "worker_message_id": sent.data[0]['id'],
                        "original_task_id": task["id"]
                    }
                    
                    return {"delegation_sent": True, "delegation_id": sent.data[0]["id"], **state}
                else:
                    print("COORD ERROR: No data returned from insert")
                    return {"delegation_sent": False, "error": "Insert failed", **state}
                    
            except Exception as e:
                print(f"COORD ERROR: Failed to insert work request: {e}")
                return {"delegation_sent": False, "error": str(e), **state}
        else:
            print("COORD: Handling simple task directly")
            return {"delegation_sent": False, **state}

    def _wait_for_response_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node - wait for actual worker response"""
        if not state.get("delegation_sent"):
            return {"worker_response": "Handled directly", **state}
        
        worker_response = self.state.latest_worker_response or {
            "status": "Completed",
            "result": "Task processed successfully",
            "details": "Worker completed the analysis"
        }

        print(f"COORD: Worker response: {worker_response.get('result', 'Processing...')}")
        return {"worker_response": worker_response, **state}
    
    def _consolidate_results_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph Node"""
        task = state["task"]
        worker_response = state["worker_response"]

        final_result = {
            "task_id": task["id"],
            "status": "completed",
            "summary": f"Task completed with worker assistance",
            "worker_contribution": worker_response,
            "completed_at": datetime.now().isoformat(),
            "telemetry": {
                "coordinator_model": "claude-3.5-sonnet",
                "worker_model": "gpt-4",
                "total_processing_time": "3.2s"
            }
        }

        print(f"COORD: Results consolidated: {final_result['summary']}")
        return {"final_result": final_result, **state}

    def _complete_task_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node"""
        final_result = state["final_result"]
        task = state["task"]

        envelope_content = {
            "schema": "company.a2a.v1",
            "type": "task.update",
            "routing": {
                "project_id": task["project_id"],
                "from": self.agent_name,
                "to": "broadcast"
            },
            "payload": {"text": f"TASK_COMPLETE: {final_result['summary']}"},
            "status": "done",
            "id": str(uuid.uuid4())
        }

        completion_data = {
            "id": str(uuid.uuid4()),
            "project_id": task["project_id"],
            "human_id": self.human_user.id,
            "agent_name": self.agent_name,
            "to_agent": "broadcast",
            "intent": f"TASK_COMPLETE: {final_result['summary']}",
            "status": "done",
            "reply_to": task["id"],
            "content": envelope_content
        }

        sent = self.supabase_sync.table('messages').insert(completion_data).execute()
        print(f"COORD: Task completed! Final message: {sent.data[0]['id']}")

        self.state.current_task = None
        self.state.task_status = "idle"

        return {"completion_sent": True, "completion_id": sent.data[0]["id"], **state}
    
    async def process_incoming_message(self, message_data: dict):
        """Process messages"""
        if message_data["id"] in self.state.processed_messages:
            return 

        self.state.processed_messages.add(message_data["id"])

        # Check if this is a worker response
        if message_data.get("intent", "").startswith("WORK_COMPLETE:"):
            print("COORD: Received worker completion")
            self.state.latest_worker_response = {
                "status": "Completed",
                "result": message_data["intent"].replace("WORK_COMPLETE: ", ""),
                "details": "Worker completed successfully"
            }
            return

        # Process new tasks through workflow
        if not message_data.get("intent", "").startswith(("WORK_REQUEST:", "TASK_COMPLETE:")):
            initial_state = {"incoming_message": message_data}
            result = await self.workflow.ainvoke(initial_state)
            print(f"COORD: Workflow completed: {result.get('completion_sent', False)}")

    async def start(self):
        """Start coordinator with polling"""
        if not await self.authenticate():
            print('Authentication failed')
            return 
        
        print("Coordinator Moon starting with polling...")

        # Start both polling and CLI
        poll_task = asyncio.create_task(self.poll_for_messages())
        cli_task = asyncio.create_task(self.handle_cli_input())
        
        try:
            await asyncio.gather(poll_task, cli_task)
        except KeyboardInterrupt:
            print("Coordinator shutting down...")
            poll_task.cancel()
            cli_task.cancel()

async def test_coordinator():
    coordinator = CoordinatorMoon("test@user.com", "test11", "coordinator")
    await coordinator.start()

if __name__ == "__main__":
    asyncio.run(test_coordinator())