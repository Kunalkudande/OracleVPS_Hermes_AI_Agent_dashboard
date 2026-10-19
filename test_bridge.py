import unittest
from bridge import validate


class BridgeTests(unittest.TestCase):
    def request(self, **updates):
        return dict(method="GET", path="/api/status", body="", cookie="", csrf="", rate_key="ab" * 32, **updates)

    def test_only_dashboard_routes(self):
        self.assertEqual(validate(self.request())["path"], "/api/status")
        for path in ["/api/config", "/api/reports/a/../../auth.json", "http://localhost/", "/api/status?url=x"]:
            data=self.request(); data["path"]=path
            with self.assertRaises(ValueError): validate(data)

    def test_headers_and_payload(self):
        data=self.request(); data["cookie"]="a\r\nInjected: true"
        with self.assertRaises(ValueError): validate(data)
        data=self.request(); data["body"]="x" * 4097
        with self.assertRaises(ValueError): validate(data)
        data=self.request(); data.update(method="POST", path="/api/login", body="[]")
        with self.assertRaises(ValueError): validate(data)
        data["body"]='{"password":"test"}'
        self.assertEqual(validate(data)["method"], "POST")


if __name__ == '__main__': unittest.main()
