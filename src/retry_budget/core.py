"""Retry budget: cap retries across a dependency so failures don't amplify load.

The core insight is that retries are not free. If a downstream dependency is
slow or failing, the requests queuing up against it plus their retries can
drive it harder than the original load ever did. Google's SRE book calls this
the retry budget. This module implements one.

Design choices made explicitly (see README for the full justification):
- The budget is set once at construction. Refilling on a schedule couples the
  throttle to wall-clock and makes it hard to reason about. A test running for
  0.1 seconds and a production server running for an hour would behave very
  differently. A fixed integer budget is deterministic: a caller can reason
  about how many retries they have.
- Allocation happens via a context manager. This mirrors how retries are
  usually taken (an ``except`` block around the retrying call) and means the
  caller cannot forget to pay for a retry they made.
- Returns are credited back by exiting the context manager normally. Raises
  are NOT credited; a retry that raised ate the token, which matches the idea
  that a failing retry is the thing you're trying to limit.
- Threading is handled by a plain mutex around allocation. Under contention
  you may see a few more denials than the budget would strictly allow; that
  is acceptable for the problem this solves.
"""

import threading
from contextlib import contextmanager


class BudgetExhausted(RuntimeError):
    """Raised when no retry token can be allocated.

    Subclassing ``RuntimeError`` instead of ``Exception`` keeps the budget's
    enforcement from accidentally swallowing ``KeyboardInterrupt``-style
    signals while still being catchable as a normal operational error.
    """


class RetryBudget:
    """A shared, thread-safe counter of retry tokens.

    Parameters
    ----------
    tokens:
        Maximum number of retries the budget will ever allow. Must be a
        non-negative integer. Zero is a valid choice and means retries are
        entirely disabled; every allocation will raise ``BudgetExhausted``.

    Examples
    --------
    >>> budget = RetryBudget(5)
    >>> for attempt in range(10):
    ...     try:
    ...         with budget.acquire():
    ...             pass  # would call the dependency here
    ...     except BudgetExhausted:
    ...         break
    """

    def __init__(self, tokens):
        if not isinstance(tokens, int) or isinstance(tokens, bool):
            raise TypeError("tokens must be an int")
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        self._tokens = tokens
        self._spent = 0
        self._lock = threading.Lock()

    @property
    def remaining(self):
        """How many tokens are still available for allocation."""
        # Snapshot under the lock so a caller doesn't read a stale partial
        # state while another thread is mid-allocation.
        with self._lock:
            return self._tokens - self._spent

    @property
    def capacity(self):
        """Total tokens the budget was initialized with."""
        return self._tokens

    def reset(self):
        """Return all spent tokens to the pool.

        There is no clock here, so no refill schedule. A caller that wants a
        time-based refill can call this from their own timer. Keeping it out
        of the budget itself means the budget is trivially testable.
        """
        with self._lock:
            self._spent = 0

    @contextmanager
    def acquire(self):
        """Take a token, run the retry block, return the token on success.

        Yields control to the body. If the body completes normally, the token
        is returned to the pool (retries that succeeded did not, in the end,
        amplify load beyond what the budget models). If the body raises, the
        token stays spent — that failing retry is exactly what the budget
        exists to throttle.

        Raises
        ------
        BudgetExhausted:
            If no token is available when allocation is attempted.
        """
        # ``acquire`` could have returned a boolean and left the caller to
        # decide what to do. The context manager is stricter: there is no way
        # for the caller to skip the allocation. If you adopt a budget you
        # should not be able to dodge it.
        with self._lock:
            if self._spent >= self._tokens:
                raise BudgetExhausted("retry budget is exhausted")
            self._spent += 1

        returned = True
        try:
            yield
        except BaseException:
            returned = False
            raise
        finally:
            # Only credit back if the block completed normally. If a
            # ``KeyboardInterrupt`` (a ``BaseException`` subclass) fires
            # mid-block we do NOT refund: the retry didn't succeed.
            if returned:
                with self._lock:
                    self._spent -= 1
