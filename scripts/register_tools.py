import os
import sys
import vertexai
from vertexai.preview import extensions

def register_mcp_as_extension(project_id: str, location: str, mcp_url: str):
    """
    Registers the MCP server as a Vertex AI Extension.
    """
    print(f"🛠️  Registering MCP Extension in {project_id}...")
    vertexai.init(project=project_id, location=location)

    # Correct schema for Vertex AI Extensions API
    try:
        extension = extensions.Extension.create(
            display_name="Customer Support Toolset",
            description="Access to billing, account, and knowledge base tools.",
            manifest={
                "name": "mcp_tools",
                "description": "Enterprise customer support tools",
                "api_spec": {
                    "open_api_yaml": f"""
openapi: 3.0.0
info:
  title: MCP Tools
  version: 1.0.0
servers:
  - url: {mcp_url}
paths:
  /mcp/tools/call:
    post:
      operationId: call_tool
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                name:
                  type: string
                arguments:
                  type: object
      responses:
        '200':
          description: OK
"""
                },
                "auth_config": {"auth_type": "NO_AUTH"}
            }
        )
        print(f"✅ Extension created: {extension.resource_name}")
    except Exception as e:
        print(f"❌ Failed to register extension: {e}")

if __name__ == "__main__":
    project = os.environ.get("GCP_PROJECT")
    mcp_url = os.environ.get("MCP_SERVER_URL")
    location = os.environ.get("GCP_LOCATION", "us-central1")
    if not project or not mcp_url:
        print("❌ GCP_PROJECT and MCP_SERVER_URL must be set")
        sys.exit(1)
    register_mcp_as_extension(project, location, mcp_url)
