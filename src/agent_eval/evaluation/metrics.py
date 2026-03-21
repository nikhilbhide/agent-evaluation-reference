from vertexai.preview.evaluation import PointwiseMetricPromptTemplate

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
