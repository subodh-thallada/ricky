import io
import json
import sys
import time
import traceback
import unittest

from fastapi.testclient import TestClient


class AuthEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from candidate_target import create_app

        cls.client = TestClient(create_app())

    def test_health_endpoint(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)

    def test_protected_endpoint_requires_auth(self):
        response = self.client.get("/protected")

        self.assertEqual(response.status_code, 401)

    def test_protected_endpoint_rejects_wrong_token(self):
        response = self.client.get(
            "/protected",
            headers={"Authorization": "Bearer wrong-token"},
        )

        self.assertEqual(response.status_code, 403)

    def test_protected_endpoint_accepts_valid_token(self):
        response = self.client.get(
            "/protected",
            headers={"Authorization": "Bearer test-token"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload)


class JsonTestResult(unittest.TextTestResult):
    pass


def _failure_payload(items):
    return [{"test": str(test), "details": details} for test, details in items]


def main():
    started = time.perf_counter()
    stream = io.StringIO()

    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(AuthEndpointTests)
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
                    "metrics": {
                        "endpoint_count": 2,
                        "auth_cases": 3,
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
