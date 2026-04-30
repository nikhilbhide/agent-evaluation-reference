import json
import hashlib
import os
import datetime
from typing import Any, Dict, List, Optional

class ABOMGenerator:
    """
    Generates an Agent Bill of Materials (ABOM) for enterprise transparency and security.
    """
    def __init__(
        self,
        agent_name: str,
        version: str,
        gsa_identity: str,
        model_name: str,
        model_version: str,
        system_instructions: str,
        tools: List[Dict[str, Any]],
        dependencies: Optional[List[str]] = None,
        eval_run_id: Optional[str] = None,
        model_armor_template: Optional[str] = None,
    ):
        self.agent_name = agent_name
        self.version = version
        self.gsa_identity = gsa_identity
        self.model_name = model_name
        self.model_version = model_version
        self.system_instructions = system_instructions
        self.tools = tools
        self.dependencies = dependencies or []
        self.eval_run_id = eval_run_id
        self.model_armor_template = model_armor_template
        self.timestamp = datetime.datetime.utcnow().isoformat() + "Z"

    def _generate_instruction_hash(self) -> str:
        # Create a canonical representation of the system instructions for hashing
        canonical_instructions = self.system_instructions.strip().lower()
        return hashlib.sha256(canonical_instructions.encode('utf-8')).hexdigest()

    def _generate_tool_hash(self) -> str:
        # Create a canonical representation of tools for hashing
        tool_str = json.dumps(self.tools, sort_keys=True)
        return hashlib.sha256(tool_str.encode('utf-8')).hexdigest()

    def generate(self) -> Dict[str, Any]:
        """
        Produces the ABOM JSON structure.
        """
        abom = {
            "bom_version": "1.1",
            "spec_version": "cyclonedx-1.5",
            "metadata": {
                "agent_name": self.agent_name,
                "agent_version": self.version,
                "timestamp": self.timestamp,
                "gsa_identity": self.gsa_identity,
                "eval_run_id": self.eval_run_id,
                "compliance": {
                    "framework": "NIST AI RMF",
                    "status": "compliant"
                }
            },
            "model": {
                "name": self.model_name,
                "version": self.model_version,
                "platform": "Vertex AI",
                "parameters": {
                    "temperature": 0.0,
                    "top_p": 0.95
                }
            },
            "governance": {
                "system_instructions_hash": self._generate_instruction_hash(),
                "tool_manifest_hash": self._generate_tool_hash(),
                "model_armor_enabled": bool(self.model_armor_template),
                "model_armor_template": self.model_armor_template,
                "security_policy": "enterprise-standard-v2",
                "data_residency": os.environ.get("GCP_LOCATION", "us-central1"),
            },
            "capabilities": {
                "total_tools": len(self.tools),
                "tools": self.tools
            },
            "supply_chain": {
                "dependencies": self.dependencies,
                "platform_version": "Agent Engine / Reasoning Engine v2",
                "binary_authorization_policy": "enforced"
            }
        }
        return abom

    def save(self, output_path: str):
        """
        Saves the ABOM to a file.
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.generate(), f, indent=2)
        print(f"✅ ABOM generated successfully at {output_path}")

def extract_tool_manifest(agent: Any, _seen: Optional[set[int]] = None) -> List[Dict[str, Any]]:
    """Walk an ADK Agent's tool tree and produce a flat manifest.

    Handles:
      * raw Python callables (function tools)
      * AgentTool wrappers (recurse into the wrapped agent's tools)
      * tool *instances* like PreloadMemoryTool (use the class name)
      * anything else falls through with a best-effort name + repr
    """
    _seen = _seen if _seen is not None else set()
    manifest: List[Dict[str, Any]] = []
    if id(agent) in _seen:
        return manifest
    _seen.add(id(agent))

    for tool in getattr(agent, "tools", []) or []:
        # AgentTool wraps another Agent — recurse so specialists' tools are
        # captured under the orchestrator's manifest.
        wrapped = getattr(tool, "agent", None)
        if wrapped is not None and hasattr(wrapped, "tools"):
            manifest.append({
                "name": getattr(wrapped, "name", repr(wrapped)),
                "kind": "agent_tool",
                "description": (getattr(wrapped, "description", None)
                                or getattr(wrapped, "instruction", "") or "")[:240],
            })
            manifest.extend(extract_tool_manifest(wrapped, _seen))
            continue

        # Callable function tool
        if callable(tool) and getattr(tool, "__name__", None):
            manifest.append({
                "name": tool.__name__,
                "kind": "function",
                "description": (tool.__doc__ or "").strip() or "No description.",
            })
            continue

        # Instantiated tool class (PreloadMemoryTool, etc.)
        cls_name = type(tool).__name__
        manifest.append({
            "name": cls_name,
            "kind": "builtin",
            "description": (type(tool).__doc__ or "").strip() or "No description.",
        })

    return manifest


def generate_default_abom(agent_name: str, version: str, system_instructions: str, tools: List[Any]):
    """
    Convenience function to generate a standard ABOM. (Legacy — prefer
    building the ABOMGenerator directly with extract_tool_manifest output.)
    """
    tool_manifest = []
    for tool in tools:
        if hasattr(tool, "__name__"):
            tool_manifest.append({
                "name": tool.__name__,
                "description": tool.__doc__ or "No description provided."
            })
        elif isinstance(tool, dict) and "name" in tool:
            tool_manifest.append({
                "name": tool["name"],
                "description": tool.get("description", "No description provided.")
            })
        else:
            tool_manifest.append({"name": str(tool), "description": "Unknown tool format"})

    deps = []
    if os.path.exists("pyproject.toml"):
        try:
            with open("pyproject.toml", "r") as f:
                if "dependencies =" in f.read():
                    deps.append("Extracted from pyproject.toml")
        except Exception:
            pass

    gen = ABOMGenerator(
        agent_name=agent_name,
        version=version,
        gsa_identity=f"agent-runtime@{os.environ.get('GCP_PROJECT', 'unknown')}.iam.gserviceaccount.com",
        model_name=os.environ.get("ORCHESTRATOR_MODEL", "gemini-2.5-pro"),
        model_version="latest",
        system_instructions=system_instructions,
        tools=tool_manifest,
        dependencies=deps,
        eval_run_id=os.environ.get("GITHUB_RUN_ID", "local-dev")
    )
    return gen.generate()
