CPP_BOUNDS: dict[str, tuple[float, float]] = {
    "economy": (0.8, 8.0),
    "business": (1.0, 25.0),
    "first": (1.5, 35.0),
}

TPG_MAX_MULTIPLIER = 2.5


def bounds_check(result: dict, cabin: str) -> dict:
    lo, hi = CPP_BOUNDS.get(cabin, (0.5, 50.0))
    cpp = result["cpp"]
    if cpp < lo:
        result["flags"].append(
            f"⛔ CPP {cpp:.2f}¢ below floor {lo}¢ — likely stale or wrong zone"
        )
        result["confidence"] = "low"
    elif cpp > hi:
        result["flags"].append(
            f"⛔ CPP {cpp:.2f}¢ above ceiling {hi}¢ — check award chart source"
        )
        result["confidence"] = "low"
    return result


def tpg_sanity_check(cpp: float, program: str, tpg_valuations: dict) -> list[str]:
    """Flag if CPP exceeds 2.5× TPG industry estimate."""
    flags: list[str] = []
    val = tpg_valuations.get(program)
    if val and cpp > val * TPG_MAX_MULTIPLIER * 100:
        flags.append(
            f"⛔ CPP {cpp:.2f}¢ exceeds 2.5× TPG estimate ({val:.2f}¢) for {program}"
        )
    return flags
