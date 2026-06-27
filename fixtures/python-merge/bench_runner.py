import io
import json
import sys
import time
import traceback
import unittest


class JsonTestResult(unittest.TextTestResult):
    def _exc_info_to_string(self, err, test):
        return super()._exc_info_to_string(err, test)


def _failure_payload(items):
    payload = []
    for test, details in items:
        payload.append(
            {
                "test": str(test),
                "details": details,
            }
        )
    return payload


def main():
    started = time.perf_counter()
    stream = io.StringIO()

    try:
        suite = unittest.defaultTestLoader.discover(".", pattern="test_candidate.py")
        runner = unittest.TextTestRunner(
            stream=stream,
            resultclass=JsonTestResult,
            verbosity=2,
        )
        result = runner.run(suite)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        failed = len(result.failures) + len(result.errors)
        passed = result.testsRun - failed - len(result.skipped)

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
                                    type(exc),
                                    exc,
                                    exc.__traceback__,
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
