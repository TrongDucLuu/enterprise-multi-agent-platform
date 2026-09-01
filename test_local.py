import asyncio
import os
import uuid
import vertexai
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
from google.adk.sessions import InMemorySessionService
from vertexai import agent_engines
from google.genai import types
from agent_core.agent import root_orchestrator

load_dotenv()

async def main():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    resource_location = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
    agent_name = os.getenv("AGENT_ENGINE_MEMORY_BANK_NAME", "agent_core")
    
    print(f"--- Initializing IT Helpdesk Agent Runner in {resource_location} ---")
    if project_id:
        try:
            vertexai.init(project=project_id, location=resource_location)
        except Exception as e:
            print(f"Warning: Vertex AI init error: {e}")

    memory_service = None
    if project_id and not os.getenv("USE_IN_MEMORY_SESSION"):
        try:
            existing = list(agent_engines.list(filter=f"display_name={agent_name}"))
            if existing:
                ae_id = existing[0].resource_name.split("/")[-1]
                memory_service = VertexAiMemoryBankService(
                    project=project_id,
                    location=resource_location,
                    agent_engine_id=ae_id
                )
                print(f"✅ Connected to persistent Vertex AI Memory Bank: {ae_id}")
        except Exception as e:
            print(f"Notice: Memory bank not connected ({e}). Using session-only mode.")

    session_service = InMemorySessionService()

    runner = Runner(
        agent=root_orchestrator,
        app_name="it-helpdesk-agent",
        session_service=session_service,
        memory_service=memory_service
    )

    user_id = "user-employee-001"
    session_id = f"session-{str(uuid.uuid4())[:8]}"

    await session_service.create_session(
        app_name="it-helpdesk-agent",
        user_id=user_id,
        session_id=session_id
    )

    print("\n" + "="*60)
    print("🎫 IT HELPDESK MULTI-AGENT SYSTEM (L1 / L2 / L3)")
    print("="*60)
    print("Mức 1: Hỏi FAQ chính sách IT, hướng dẫn reset password, tạo ticket.")
    print("Mức 2: Tra cứu tài liệu ERP (SAP), HRM (Workday), CRM (Salesforce), soạn email.")
    print("Mức 3: Gửi log file để làm Root Cause Analysis (RCA) hoặc rà soát hợp đồng SLA.")
    print("Lệnh: 'new' để tạo phiên mới, 'quit' hoặc 'exit' để thoát.")
    print(f"--- Active Session ID: {session_id} (User: {user_id}) ---\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit"]:
            print("Goodbye!")
            break

        if user_input.lower() == "new":
            session_id = f"session-{str(uuid.uuid4())[:8]}"
            await session_service.create_session(
                app_name="it-helpdesk-agent",
                user_id=user_id,
                session_id=session_id
            )
            print(f"\n--- Fresh Session Started (ID: {session_id}) ---")
            continue

        print("\nAgent is analyzing & coordinating...")
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(parts=[types.Part(text=user_input)])
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        print(f"Agent: {part.text}")

            if event.get_function_calls():
                for fc in event.get_function_calls():
                    print(f"🛠️  Tool Call: {fc.name}")

if __name__ == "__main__":
    asyncio.run(main())
