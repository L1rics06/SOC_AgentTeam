# OpenSearch Investigation

Use OpenSearch tools when the current alert is not enough.

Recommended query pattern:

- Search by event text plus host, user, and IP entities.
- Aggregate a timeline around the alert timestamp.
- Search Security Analytics findings before broad event search.
- Search knowledge/playbook context for IOC, alert type, or recommended response.

Returned hits are evidence candidates, not facts until you cite why they support the conclusion.

