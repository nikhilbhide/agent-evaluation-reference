from agent_eval.agent.core import run_customer_resolution_agent
from unittest.mock import patch, MagicMock

@patch("agent_eval.agent.core.GenerativeModel")
def test_run_customer_resolution_agent_success(mock_gen_model):
    mock_model_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Routing to billing_agent."
    mock_model_instance.generate_content.return_value = mock_response
    mock_gen_model.return_value = mock_model_instance

    result = run_customer_resolution_agent("I need a refund.")
    assert result == "Routing to billing_agent."
    mock_model_instance.generate_content.assert_called_once_with("I need a refund.")

@patch("agent_eval.agent.core.GenerativeModel")
def test_run_customer_resolution_agent_error(mock_gen_model):
    mock_model_instance = MagicMock()
    mock_model_instance.generate_content.side_effect = Exception("API error")
    mock_gen_model.return_value = mock_model_instance

    result = run_customer_resolution_agent("I need a refund.")
    assert "Agent Error: API error" in result
