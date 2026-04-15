def fetch_user_profile(user_id: str) -> str:
    return f"user-{user_id}"


class UserCardService:
    def build_card(self, user_id: str) -> str:
        profile = fetch_user_profile(user_id)
        return f"card:{profile}"
