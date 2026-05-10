from app.opensearch_adapter import OpenSearchAdapter


def test_build_event_query_includes_entities_and_time_window():
    query = OpenSearchAdapter.build_event_query(
        {
            "text": "failed login",
            "hosts": ["win-finance-07"],
            "users": ["zhang.wei"],
            "ips": ["203.0.113.77"],
            "start": "2026-05-10T08:00:00Z",
            "end": "2026-05-10T09:00:00Z",
        }
    )

    bool_query = query["query"]["bool"]
    assert bool_query["must"][0]["multi_match"]["query"] == "failed login"
    assert {"terms": {"host.name": ["win-finance-07"]}} in bool_query["filter"]
    assert {"terms": {"user.name": ["zhang.wei"]}} in bool_query["filter"]
    assert {"range": {"@timestamp": {"gte": "2026-05-10T08:00:00Z", "lte": "2026-05-10T09:00:00Z"}}} in bool_query["filter"]

