"""Shared JSON delta wire format; also packaged with the independent seats API."""

DELTA = 'dashboard-json-delta'


def changes(before, after, path=()):
    """Index-addressed edits preserve null values and explicitly carry deletions."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = [['remove', [*path, key]] for key in before if key not in after]
        for key, value in after.items():
            result += changes(before[key], value, (*path, key)) if key in before else [['set', [*path, key], value]]
        return result
    if isinstance(before, list) and isinstance(after, list):
        result = []
        for i in range(min(len(before), len(after))):
            result += changes(before[i], after[i], (*path, i))
        result += [['remove', [*path, i]] for i in range(len(before)-1, len(after)-1, -1)]
        result += [['set', [*path, i], after[i]] for i in range(len(before), len(after))]
        return result
    return [['set', list(path), after]]
