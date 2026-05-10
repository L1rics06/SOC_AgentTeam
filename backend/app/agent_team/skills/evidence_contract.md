# Evidence Contract

Every claim must be tied to one of these evidence classes:

- Original alert payload and its evidence_refs.
- OpenSearch documents returned by tools.
- OpenSearch timeline or findings aggregations returned by tools.
- Internal knowledge or playbook matches returned by vector search.
- Explicit absence of evidence, framed as a limitation.

Do not turn assumptions into facts. If a key field is missing, name the gap and reduce confidence.

