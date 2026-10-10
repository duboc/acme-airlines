"""Sales agent: a separate A2A service, structurally unable to contact passengers.

Its only channels: the Customer agent's A2A skills (`acme.sales.client`) and the read-only
`inventory` MCP server with the `sales` credential (`acme.sales.config`).
"""
