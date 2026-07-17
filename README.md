# NamiFusion SDK

Official SDKs for the [NamiFusion](https://www.namifusion.com) AI model marketplace API.

Repository: https://github.com/shanweimu/namifusion-sdk

## Packages

| Package | Language | Install |
| --- | --- | --- |
| [`@namifusion/client`](packages/typescript/README.md) | TypeScript / JavaScript | `npm install @namifusion/client` |
| [`namifusion`](packages/python/README.md) | Python | `pip install namifusion` |

## Quick start

### TypeScript

```ts
import { NamiFusion } from "@namifusion/client";

// Reads the API key from the NAMIFUSION_API_KEY environment variable.
const client = new NamiFusion();

const task = await client.subscribe(modelId, {
  input: { /* model-specific input */ },
});
```

### Python

```python
from namifusion import NamiFusion

# Reads the API key from the NAMIFUSION_API_KEY environment variable.
client = NamiFusion()

task = client.subscribe(model_id, input={...})
```

## Documentation

- TypeScript package: [packages/typescript/README.md](packages/typescript/README.md)
- Python package: [packages/python/README.md](packages/python/README.md)
