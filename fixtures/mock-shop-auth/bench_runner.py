import io
import json
import sys
import time
import traceback
import unittest


class JsonTestResult(unittest.TextTestResult):
    pass


def _failure_payload(items):
    return [{"test": str(test), "details": details} for test, details in items]


# Module-agnostic convention shared by every mock-shop fixture: test methods named
# test_feature_* exercise the requested feature (lockout / rollback / idempotency /
# ...), everything else is pre-existing behavior we must not regress. Surfaced as
# metrics so the UI can show what actually got exercised.
FEATURE_PREFIXES = ("test_feature",)


def _split_counts(result):
    feature = regression = 0
    for test in getattr(result, "collected", []):
        name = test._testMethodName
        if name.startswith(FEATURE_PREFIXES):
            feature += 1
        else:
            regression += 1
    return feature, regression


class CollectingResult(JsonTestResult):
    def startTest(self, test):
        super().startTest(test)
        if not hasattr(self, "collected"):
            self.collected = []
        self.collected.append(test)


def main():
    started = time.perf_counter()
    stream = io.StringIO()

    try:
        suite = unittest.defaultTestLoader.discover(".", pattern="test_candidate.py")
        runner = unittest.TextTestRunner(
            stream=stream,
            resultclass=CollectingResult,
            verbosity=2,
        )
        result = runner.run(suite)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        failed = len(result.failures) + len(result.errors)
        passed = result.testsRun - failed - len(result.skipped)
        feature, regression = _split_counts(result)

        print(stream.getvalue(), end="")
        print(
            json.dumps(
                {
                    "tests": {
                        "passed": passed,
                        "failed": failed,
                        "total": result.testsRun,
                    },
                    "failures": _failure_payload(result.failures),
                    "errors": _failure_payload(result.errors),
                    "duration_ms": duration_ms,
                    "metrics": {
                        "feature_tests": feature,
                        "regression_tests": regression,
                        "package": "mock_shop",
                    },
                },
                sort_keys=True,
            )
        )
        return 0 if result.wasSuccessful() else 1
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        print(
            json.dumps(
                {
                    "tests": {"passed": 0, "failed": 1, "total": 1},
                    "failures": [],
                    "errors": [
                        {
                            "test": "bench_runner",
                            "details": "".join(
                                traceback.format_exception(
                                    type(exc), exc, exc.__traceback__
                                )
                            ),
                        }
                    ],
                    "duration_ms": duration_ms,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
