# Shared Discord virtual-channel queue state.
# Lives here (not in helpers.py or executor.py) to break the circular import:
#   helpers._slack_post writes to this dict
#   commands.executor._handle_discord_command reads from this dict
_discord_queues: dict = {}
