# Tool modules are auto-discovered by src/server.py at startup.
# Each module must export a register(mcp, client) function.
# Do NOT import tool modules here — that breaks the auto-discovery pattern.
