import claude_client
import config
import pytest
from types import SimpleNamespace

def test_calculate_response_cost_non_zero():
    response = SimpleNamespace(
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500)
        )
    cost_tuple = (0.001, 0.0025) # (input_cost, output_cost) assuming Haiku default
    assert claude_client.calculate_response_cost(response) == cost_tuple
