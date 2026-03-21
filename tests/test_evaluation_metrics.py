from agent_eval.evaluation.metrics import get_resolution_metric

def test_get_resolution_metric():
    metric = get_resolution_metric()
    assert metric is not None
    assert type(metric).__name__ == "PointwiseMetricPromptTemplate"
