"""Small parser for model option inventories returned by ComfyUI object_info."""


def combo_options(descriptor):
    """Return legacy/v3 string options, [] for an empty inventory, None if unknown."""
    if not isinstance(descriptor, (list, tuple)) or not descriptor:
        return None
    choices = descriptor[0]
    if choices == 'COMBO' and len(descriptor) > 1 and isinstance(descriptor[1], dict):
        choices = descriptor[1].get('options')
    if not isinstance(choices, (list, tuple)) or any(not isinstance(choice, str) for choice in choices):
        return None
    return choices
