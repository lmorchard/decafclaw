"""Print the agent data dir `make dev` should watch (#149).

The watchfiles CLI takes watch paths as arguments, but the agent config
files live under DATA_HOME/AGENT_ID — which .env may point outside this
checkout (worktrees share the main clone's data/). `make dev` resolves the
dir here, creates it if missing (watchfiles refuses a nonexistent path),
and passes it to watchfiles.
"""

from .config_watcher import agent_data_dir, load_env_file


def main() -> None:
    load_env_file()
    agent_dir = agent_data_dir()
    agent_dir.mkdir(parents=True, exist_ok=True)
    print(agent_dir)


if __name__ == "__main__":
    main()
