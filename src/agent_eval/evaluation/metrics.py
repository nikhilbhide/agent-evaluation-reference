"""Evaluation metric definitions.

The runner combines three categories:
  1. SDK built-in pointwise metrics (passed by string name to ``EvalTask``):
     ``safety``, ``groundedness``, ``instruction_following``,
     ``question_answering_quality``, ``text_quality``. These cover the
     console's "Safety", "Hallucination" (via groundedness), and
     "Final Response Quality" surfaces.
  2. The custom resolution rubric below — routing + helpfulness for the
     customer-support domain.
  3. (Not yet covered) Agent-specific metrics from the Vertex AI Agent
     Evaluation console — "Tool Use Quality", "Multi-turn Task Success",
     "Multi-turn Tool Use Quality", "Multi-turn Trajectory Quality". These
     are not yet exposed by ``vertexai.preview.evaluation`` (as of
     google-cloud-aiplatform 1.148.x); they require the newer Gen AI
     Eval Service v2 REST surface. Wire those in once the SDK ships them.
"""

from vertexai.preview.evaluation import PointwiseMetricPromptTemplate

# Built-in pointwise metric names accepted as strings by ``EvalTask``.
# Keep this list in sync with ``runner.evaluate_agent`` and the BigQuery
# schema in ``scripts/setup_telemetry_sink.py``.
BUILTIN_METRICS: list[str] = [
    "safety",
    "groundedness",
    "instruction_following",
    "question_answering_quality",
    "text_quality",
]


def get_resolution_metric() -> PointwiseMetricPromptTemplate:
    """Returns the custom pointwise metric template for resolution grading."""
    return PointwiseMetricPromptTemplate(
        criteria={
            "Routing & Tool Accuracy": "Does the response explicitly state it would route to the correct department and tool as defined in the reference?",
            "Helpfulness & Empathy": "Does the response effectively and politely address the user prompt matching the reference outcome?"
        },
        rating_rubric={
            "1": "Fails to meet expected routing or completely unhelpful.",
            "3": "Routes correctly but provides incomplete resolution or lacks empathy.",
            "5": "Perfect routing, perfect tool use, and empathetic resolution matching the reference."
        }
    )
