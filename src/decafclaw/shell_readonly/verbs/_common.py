"""Small argument-inspection helpers shared by verb contracts (#1641)."""


def short_flag_present(args: list[str], letters: str) -> bool:
    """True when a short-option cluster contains any of ``letters``."""
    for token in args:
        if token.startswith("-") and not token.startswith("--") and len(token) > 1:
            if any(letter in token[1:] for letter in letters):
                return True
    return False


def flag_name(token: str) -> str:
    """The flag name of ``--name=value`` or a plain token."""
    return token.split("=", 1)[0]


def has_files0_from(args: list[str]) -> bool:
    """True when ``--files0-from`` names a list whose paths are unchecked."""
    return any(token == "--files0-from" or token.startswith("--files0-from=") for token in args)


def has_files_from(args: list[str]) -> bool:
    """True when GNU ``file`` reads targets from a list file."""
    return any(token == "--files-from" or token.startswith("--files-from=") for token in args)
