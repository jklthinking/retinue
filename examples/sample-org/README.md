# Sample organization

Generate a deterministic workspace with one node, three agents, six task cards,
and seven days of token data:

```bash
python examples/sample-org/seed.py --seed 42 --output /tmp/retinue-sample
retinue panel /tmp/retinue-sample
```

The generator refuses to overwrite a non-empty directory.
