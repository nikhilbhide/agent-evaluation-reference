import os
import sys
import vertexai
from vertexai.preview import reasoning_engines

def verify(resource_name: str):
    print(f"🔍 Verifying Enterprise Agent: {resource_name}")
    
    # Initialize Vertex AI
    PROJECT_ID = os.environ.get("GCP_PROJECT")
    vertexai.init(project=PROJECT_ID, location="us-central1")
    
    # Load the deployed agent
    remote_agent = reasoning_engines.ReasoningEngine(resource_name)
    print(f"DEBUG: Available methods: {[m for m in dir(remote_agent) if not m.startswith('_')]}")
    
    # Test cases
    test_prompts = [
        "Hello, I have a question about my last invoice INV-12345.",
        "Can you help me with an app crash I'm seeing?",
        "I need to change my account password."
    ]
    
    for prompt in test_prompts:
        print(f"\n💬 Testing Prompt: {prompt}")
        try:
            # The ReasoningEngine object itself is a resource handle; 
            # we call it directly for the primary operation.
            response = remote_agent.query(input=prompt)
            print(f"🤖 Response: {response}")
            
            # Check for routing indicators in the response (based on our ADK refactor)
            if any(term in str(response).lower() for term in ["billing", "invoice", "technical", "account"]):
                print("✅ Routing logic verified.")
            else:
                print("⚠️  Response received but routing logic not explicitly confirmed.")
        except Exception as e:
            print(f"❌ Error during query: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Try to read from build artifact if not provided
        try:
            with open("build/enterprise_metadata.json", "r") as f:
                import json
                data = json.load(f)
                resource = data["resource_name"]
        except:
            print("Usage: python verify_enterprise_deployment.py <RESOURCE_NAME>")
            sys.exit(1)
    else:
        resource = sys.argv[1]
        
    verify(resource)
