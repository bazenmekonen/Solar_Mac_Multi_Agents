import os
import asyncio
import uuid
import time
from dotenv import load_dotenv
from datetime import datetime
from supabase import create_client
from typing import Dict, Any, List
from langgraph.graph import StateGraph, END
from backend.envelope import SolarEnvelopeBuilder, SolarDatabaseAdapter
import anthropic

class WorkerState:
    def __init__(self):
        self.current_request = None
        self.analysis_result = None
        self.processed_messages = set()
        self.ai_response = None

class EnhancedWorkerMoon:
    def __init__(self, human_email: str, human_password: str, agent_name: str = "worker"):
        load_dotenv()
        self.supabase_sync = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY"))
        self.adapter = SolarDatabaseAdapter(self.supabase_sync)
        self.human_email = human_email
        self.human_password = human_password
        self.agent_name = agent_name
        self.human_user = None
        self.state = WorkerState()
        self.workflow = self._build_workflow()
        
        # Initialize AI client (Anthropic Claude)
        self.ai_client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY")
        )

    def _build_workflow(self) -> StateGraph:
        """LangGraph workflow for processing work requests"""
        workflow = StateGraph(Dict[str, Any])

        workflow.add_node("receive_request", self._receive_request_node)
        workflow.add_node("analyze_request", self._analyze_request_node)
        workflow.add_node("call_ai_model", self._call_ai_model_node)
        workflow.add_node("format_response", self._format_response_node)
        workflow.add_node("send_result", self._send_result_node)
        
        workflow.set_entry_point("receive_request")
        workflow.add_edge("receive_request", "analyze_request")
        workflow.add_edge("analyze_request", "call_ai_model")
        workflow.add_edge("call_ai_model", "format_response")
        workflow.add_edge("format_response", "send_result")
        workflow.add_edge("send_result", END)

        return workflow.compile()

    async def authenticate(self):
        """Authenticate user"""
        auth_resp = self.supabase_sync.auth.sign_in_with_password({
            "email": self.human_email,
            "password": self.human_password
        })

        if auth_resp.session:
            self.human_user = auth_resp.user
            print(f"Enhanced Worker authenticated: {self.human_user.email}")
            return True
        return False

    def _receive_request_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node: Process incoming work request"""
        request_message = state.get("work_request")
        if not request_message:
            return {"status": "no_request"}
        
        self.state.current_request = {
            "id": request_message["id"],
            "text": request_message["intent"].replace("WORK_REQUEST: ", ""),
            "project_id": request_message["project_id"],
            "original_message": request_message
        }

        print(f"WORKER: Received work request: {self.state.current_request['text']}")
        return {"status": "request_received", "request": self.state.current_request}

    def _analyze_request_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node: Analyze the type of work requested"""
        request = state["request"]
        request_text = request["text"].lower()
        
        # Determine the type of analysis needed
        if any(keyword in request_text for keyword in ["security", "vulnerability", "exploit"]):
            analysis_type = "security_review"
            prompt_template = "security_analysis"
        elif any(keyword in request_text for keyword in ["code", "review", "bug", "error"]):
            analysis_type = "code_review"
            prompt_template = "code_analysis"
        elif any(keyword in request_text for keyword in ["test", "testing", "qa"]):
            analysis_type = "test_analysis"
            prompt_template = "test_strategy"
        else:
            analysis_type = "general_analysis"
            prompt_template = "general_task"

        analysis = {
            "type": analysis_type,
            "prompt_template": prompt_template,
            "complexity": "high" if len(request["text"]) > 50 else "medium",
            "estimated_time": time.time()
        }

        print(f"WORKER: Analysis type: {analysis_type}")
        return {"analysis": analysis, **state}

    def _call_ai_model_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node: Call AI model to process the request"""
        request = state["request"]
        analysis = state["analysis"]
        
        # Build prompt based on analysis type
        prompts = {
            "security_analysis": f"""
            You are a senior security analyst. Analyze the following request and provide a comprehensive security assessment:

            Request: {request['text']}

            Provide:
            1. Security considerations and potential vulnerabilities
            2. Risk assessment (High/Medium/Low)
            3. Specific recommendations
            4. Implementation steps

            Be thorough and actionable in your response.
            """,
                        "code_analysis": f"""
            You are a senior software engineer conducting a code review. Analyze the following request:

            Request: {request['text']}

            Provide:
            1. Code quality assessment
            2. Potential bugs and issues
            3. Performance considerations
            4. Best practices recommendations
            5. Refactoring suggestions

            Give specific, actionable feedback.
            """,
                        "test_strategy": f"""
            You are a QA engineer. Analyze the following testing request:

            Request: {request['text']}

            Provide:
            1. Testing strategy
            2. Test cases to consider
            3. Tools and frameworks recommendations
            4. Coverage analysis
            5. Risk areas to focus on

            Be comprehensive and practical.
            """,
                        "general_task": f"""
            You are a technical consultant. Analyze the following request and provide expert guidance:

            Request: {request['text']}

            Provide:
            1. Analysis of the request
            2. Approach recommendations
            3. Potential challenges
            4. Implementation guidance
            5. Success criteria

            Be thorough and professional.
            """
        }

        prompt = prompts.get(analysis["prompt_template"], prompts["general_task"])
        
        try:
            print(f"WORKER: Calling AI model for {analysis['type']}...")
            start_time = time.time()
            
            # Call Anthropic Claude
            message = self.ai_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
                temperature=0.3,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            processing_time = int((time.time() - start_time) * 1000)
            ai_response = message.content[0].text
            
            print(f"WORKER: AI analysis completed in {processing_time}ms")
            
            self.state.ai_response = {
                "content": ai_response,
                "model": "claude-3-sonnet-20240229",
                "processing_time_ms": processing_time,
                "tokens_used": message.usage.input_tokens + message.usage.output_tokens,
                "analysis_type": analysis["type"]
            }
            
            return {"ai_response": self.state.ai_response, **state}
            
        except Exception as e:
            print(f"WORKER ERROR: AI call failed: {e}")
            # Fallback response
            fallback_response = f"Analysis completed for '{request['text'][:50]}...'. Unable to connect to AI model, providing basic analysis."
            
            self.state.ai_response = {
                "content": fallback_response,
                "model": "fallback",
                "processing_time_ms": 100,
                "error": str(e),
                "analysis_type": analysis["type"]
            }
            
            return {"ai_response": self.state.ai_response, **state}

    def _format_response_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node: Format the AI response for sending back"""
        ai_response = state["ai_response"]
        analysis = state["analysis"]
        request = state["request"]
        
        # Format the response with metadata
        formatted_response = {
            "summary": f"AI-powered {analysis['type']} completed",
            "detailed_analysis": ai_response["content"],
            "metadata": {
                "model_used": ai_response["model"],
                "processing_time_ms": ai_response["processing_time_ms"],
                "analysis_type": ai_response["analysis_type"],
                "request_id": request["id"],
                "worker_agent": self.agent_name,
                "completed_at": datetime.now().isoformat()
            }
        }
        
        # Create concise summary for the intent field
        summary_text = f"{formatted_response['summary']}: {ai_response['content'][:100]}..."
        
        print(f"WORKER: Response formatted - {len(ai_response['content'])} characters")
        return {"formatted_response": formatted_response, "summary_text": summary_text, **state}

    def _send_result_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """LangGraph node: Send result back to coordinator"""
        request = state["request"]
        formatted_response = state["formatted_response"]
        summary_text = state["summary_text"]
        
        # Send completion back to coordinator
        envelope_content = {
            "schema": "company.a2a.v1",
            "type": "task.update",
            "routing": {
                "project_id": request["project_id"],
                "from": self.agent_name,
                "to": "coordinator"
            },
            "payload": {"text": f"WORK_COMPLETE: {summary_text}"},
            "status": "done",
            "id": str(uuid.uuid4())
        }
        
        completion_data = {
            "id": str(uuid.uuid4()),
            "project_id": request["project_id"],
            "human_id": self.human_user.id,
            "agent_name": self.agent_name,
            "to_agent": "coordinator",
            "intent": f"WORK_COMPLETE: {summary_text}",
            "status": "done",
            "reply_to": request["id"],
            "content": envelope_content,
            "telemetry": formatted_response["metadata"]
        }
        
        try:
            sent = self.supabase_sync.table('messages').insert(completion_data).execute()
            print(f"WORKER: AI-powered result sent: {sent.data[0]['id']}")
            
            # Reset state
            self.state.current_request = None
            self.state.ai_response = None
            
            return {"result_sent": True, "result_id": sent.data[0]["id"], **state}
            
        except Exception as e:
            print(f"WORKER ERROR: Failed to send result: {e}")
            return {"result_sent": False, "error": str(e), **state}

    async def process_work_request(self, message_data: dict):
        """Process work request through LangGraph workflow"""
        if message_data["id"] in self.state.processed_messages:
            print(f"WORKER: Already processed {message_data['id'][:8]}...")
            return

        self.state.processed_messages.add(message_data["id"])
        
        # Run the work request through the LangGraph workflow
        initial_state = {"work_request": message_data}
        
        try:
            result = await self.workflow.ainvoke(initial_state)
            print(f"WORKER: Workflow completed: {result.get('result_sent', False)}")
        except Exception as e:
            print(f"WORKER ERROR: Workflow failed: {e}")

    async def poll_for_work_requests(self):
        """Poll for work requests from coordinator"""
        last_checked = datetime.now()
        
        while True:
            try:
                # Check specifically for worker messages
                worker_messages = self.supabase_sync.table('messages').select('*').filter(
                    'to_agent', 'eq', 'worker'
                ).filter(
                    'created', 'gte', last_checked.isoformat()
                ).order('created', desc=False).execute()
                
                print(f"WORKER: Polling found {len(worker_messages.data)} new messages")
                
                for message in worker_messages.data:
                    if (message['id'] not in self.state.processed_messages and 
                        message.get('intent', '').startswith('WORK_REQUEST:')):
                        print(f"WORKER: Processing work request via AI workflow...")
                        await self.process_work_request(message)
                
                last_checked = datetime.now()
                await asyncio.sleep(3)
                
            except Exception as e:
                print(f"Worker polling error: {e}")
                await asyncio.sleep(5)

    async def start(self):
        """Start enhanced worker with AI capabilities"""
        if not await self.authenticate():
            print('Worker authentication failed')
            return 
        
        print("Enhanced AI Worker Moon starting...")
        print("Waiting for work requests to process with AI...")

        poll_task = asyncio.create_task(self.poll_for_work_requests())
        
        try:
            await poll_task
        except KeyboardInterrupt:
            print("Enhanced AI Worker shutting down...")
            poll_task.cancel()

async def test_enhanced_worker():
    worker = EnhancedWorkerMoon("test@user.com", "test11", "worker")
    await worker.start()

if __name__ == "__main__":
    asyncio.run(test_enhanced_worker())