from dataclasses import dataclass

@dataclass
class FrictionTheme:
    theme: str
    proposed_addition: str
    occurrences: int

async def analyze_friction(config) -> list[FrictionTheme]:
    return []
