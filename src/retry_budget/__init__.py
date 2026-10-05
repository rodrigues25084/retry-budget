# Retry Budget: a shared retry budget so a failing dependency cannot amplify load.

from .core import RetryBudget, BudgetExhausted

__all__ = ["RetryBudget", "BudgetExhausted"]
