import json
from typing import List
from accounts.models import Expertise


def parse_tagify_input(raw_value: str) -> List[str]:
    """
    Tagify sends JSON like:
    '[{"value":"Web Development"},{"value":"Networking"}]'
    """
    if not raw_value:
        return []

    raw_value = raw_value.strip()

    if raw_value.startswith("["):
        try:
            data = json.loads(raw_value)
            return [
                item.get("value", "").strip()
                for item in data
                if item.get("value")
            ]
        except Exception:
            return []

    return [x.strip() for x in raw_value.split(",") if x.strip()]


def save_user_expertise(user, raw_expertise: str):
    """
    stores in:
    accounts_expertise
    accounts_user_domain_of_expertise
    """
    names = parse_tagify_input(raw_expertise)

    # remove old expertise relations
    user.domain_of_expertise.clear()

    for name in names:
        if not name:
            continue

        name = name.strip()

        # 1) exact match
        exp_obj = Expertise.objects.filter(name__iexact=name).first()

        # 2) partial match
        if not exp_obj:
            exp_obj = Expertise.objects.filter(name__icontains=name).first()

        # 3) create new if nothing matched
        if not exp_obj:
            exp_obj = Expertise.objects.create(name=name)

        # create row in accounts_user_domain_of_expertise
        user.domain_of_expertise.add(exp_obj)

    return names
