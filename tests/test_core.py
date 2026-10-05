import unittest

from retry_budget import RetryBudget, BudgetExhausted


class TestConstruction(unittest.TestCase):
    def test_accepts_non_negative_int(self):
        budget = RetryBudget(3)
        self.assertEqual(budget.capacity, 3)
        self.assertEqual(budget.remaining, 3)

    def test_zero_is_valid_and_blocks_all_retries(self):
        budget = RetryBudget(0)
        self.assertEqual(budget.capacity, 0)
        self.assertEqual(budget.remaining, 0)
        with self.assertRaises(BudgetExhausted):
            with budget.acquire():
                self.fail("should not have entered the block")

    def test_rejects_negative(self):
        with self.assertRaises(ValueError):
            RetryBudget(-1)

    def test_rejects_float(self):
        # Floats would make allocation and accounting fuzzy; the budget is an
        # integer count of retries by design.
        with self.assertRaises(TypeError):
            RetryBudget(1.5)

    def test_rejects_string(self):
        with self.assertRaises(TypeError):
            RetryBudget("3")

    def test_rejects_bool(self):
        # bool is a subclass of int in Python; accepting True/False as a
        # token count would be a surprising footgun.
        with self.assertRaises(TypeError):
            RetryBudget(True)


class TestAllocation(unittest.TestCase):
    def test_successful_retry_refunds_token(self):
        budget = RetryBudget(2)
        with budget.acquire():
            pass
        self.assertEqual(budget.remaining, 2)
        self.assertEqual(budget.capacity, 2)

    def test_failing_retry_keeps_token(self):
        budget = RetryBudget(2)
        with self.assertRaises(ValueError):
            with budget.acquire():
                raise ValueError("downstream failed")
        self.assertEqual(budget.remaining, 1)

    def test_refund_is_exactly_one_per_attempt(self):
        budget = RetryBudget(2)
        for _ in range(5):
            with budget.acquire():
                pass
        self.assertEqual(budget.remaining, 2)

    def test_exhaustion_is_raised_not_silently_skipped(self):
        budget = RetryBudget(1)
        with self.assertRaises(ValueError):
            with budget.acquire():
                raise ValueError("this retry failed; token stays spent")
        self.assertEqual(budget.remaining, 0)
        with self.assertRaises(BudgetExhausted):
            with budget.acquire():
                self.fail("no token should have been available")

    def test_interleaved_success_and_failure_accounting(self):
        budget = RetryBudget(4)
        # fail, fail, succeed, fail -> 3 spent
        with self.assertRaises(ValueError):
            with budget.acquire():
                raise ValueError()
        with self.assertRaises(ValueError):
            with budget.acquire():
                raise ValueError()
        with budget.acquire():
            pass
        with self.assertRaises(ValueError):
            with budget.acquire():
                raise ValueError()
        self.assertEqual(budget.remaining, 1)

    def test_budget_can_be_exactly_consumed_by_failures(self):
        budget = RetryBudget(2)
        with self.assertRaises(ValueError):
            with budget.acquire():
                raise ValueError()
        with self.assertRaises(ValueError):
            with budget.acquire():
                raise ValueError()
        self.assertEqual(budget.remaining, 0)


class TestReset(unittest.TestCase):
    def test_reset_restores_full_budget(self):
        budget = RetryBudget(3)
        for _ in range(3):
            with self.assertRaises(ValueError):
                with budget.acquire():
                    raise ValueError()
        self.assertEqual(budget.remaining, 0)
        budget.reset()
        self.assertEqual(budget.remaining, 3)

    def test_reset_on_unused_budget_is_noop(self):
        budget = RetryBudget(5)
        budget.reset()
        self.assertEqual(budget.remaining, 5)


class TestConcurrency(unittest.TestCase):
    def test_concurrent_allocations_do_not_exceed_budget(self):
        # Under contention the lock guarantees we never hand out more tokens
        # than the budget allows.
        budget = RetryBudget(100)
        from concurrent.futures import ThreadPoolExecutor
        acquired = 0
        lock = __import__("threading").Lock()

        def worker():
            nonlocal acquired
            try:
                with budget.acquire():
                    with lock:
                        acquired += 1
                    raise ValueError("simulate failing retry")
            except (BudgetExhausted, ValueError):
                return

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(lambda _: worker(), range(1000)))

        self.assertEqual(acquired, 100)
        self.assertEqual(budget.remaining, 0)


class TestTypes(unittest.TestCase):
    def test_remaining_is_int_not_float(self):
        budget = RetryBudget(3)
        self.assertIsInstance(budget.remaining, int)

    def test_capacity_is_int(self):
        budget = RetryBudget(3)
        self.assertIsInstance(budget.capacity, int)

    def test_budget_exhausted_is_runtime_error(self):
        # Callers may want to catch this alongside other operational errors.
        self.assertTrue(issubclass(BudgetExhausted, RuntimeError))


if __name__ == "__main__":
    unittest.main()
