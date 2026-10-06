# Retry Budget

A small library that hands out a bounded number of retry tokens and refuses to hand out more. A failing dependency cannot amplify load past the budget you set.

## Usage

```python
from retry_budget import RetryBudget, BudgetExhausted

budget = RetryBudget(10)  # at most 10 retries across the whole process

attempts = [None, None, "ok"]  # stand-ins for real responses; first two fail

for attempt in attempts:
    try:
        with budget.acquire():
            response = attempt
        break
    except BudgetExhausted:
        # no retry budget left; propagate the original failure instead
        raise
    except TypeError:
        # response was None, treat as a transient error and retry
        continue
```

`acquire()` is a context manager. The block runs only if a token was available. Exiting the block normally returns the token; exiting via an exception keeps it spent.

## Why this exists

When a downstream service degrades, retries pile up against it. The original request plus N retries can drive the failing dependency harder than the load that started the problem. A retry budget caps how much amplification can happen. Once the budget is empty, callers see `BudgetExhausted` and must propagate the failure rather than retry.

The trade-off is explicit: this library is **not** a time-based rate limiter and does not refill on a schedule. A fixed integer budget is easy to reason about and easy to test. If you want a refill, call `budget.reset()` from your own timer. Keeping the clock out of the budget means behaviour does not depend on how long the process has been running.

## Edge cases you will hit

- `tokens=0` is valid and disables all retries; every `acquire()` raises `BudgetExhausted`. Use it to wire a budget into code that may later be turned on.
- A retry that raises keeps its token. A retry that returns its token on success is the model: a successful retry did not, in the end, add load beyond the original attempt.
- `bool` is rejected as a token count even though it subclasses `int` in Python. `RetryBudget(True)` raises `TypeError`; use `RetryBudget(1)`.
- Under thread contention the budget may deny a few calls that would strictly have fit. It will never hand out more tokens than the budget allows.
