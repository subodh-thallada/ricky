PROFILE_SOURCE = {
    1: {"id": 1, "name": "Ava Patel", "role": "student"},
    2: {"id": 2, "name": "Milo Chen", "role": "student"},
    3: {"id": 3, "name": "Sara Johnson", "role": "admin"},
}


def fetch_profile_from_source(user_id: int) -> dict[str, object]:
    profile = PROFILE_SOURCE.get(user_id)
    if profile is None:
        raise KeyError(f"unknown user id: {user_id}")
    return dict(profile)


def get_user_profile(user_id: int) -> dict[str, object]:
    """Bench demo: compare a few correct caching strategies for this lookup."""
    raise NotImplementedError("Ask Bench: add caching to this profile loader (test)")
