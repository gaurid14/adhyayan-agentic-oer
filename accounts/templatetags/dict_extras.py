"""
accounts/templatetags/dict_extras.py

Custom template filters for dict lookups that Django's default
template engine doesn't support out of the box.

Usage in templates:
    {% load dict_extras %}
    {% with val=my_dict|get_item:some_key %}...{% endwith %}
"""

from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Look up a value in a dict by a dynamic key.
    Returns None if the key is missing.
    """
    if not isinstance(dictionary, dict):
        return None
    return dictionary.get(key)
