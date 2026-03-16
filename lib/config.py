"""Config loading from ~/.context-hooks/config.yaml and per-project overrides."""
import os
import re


def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML parser for flat/shallow config. No external deps."""
    result = {}
    current_list_key = None
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            current_list_key = None
            continue
        if stripped.startswith('- ') and current_list_key:
            result.setdefault(current_list_key, []).append(stripped[2:].strip())
            continue
        m = re.match(r'^([\w][\w.-]*)\s*:\s*(.*)', stripped)
        if m:
            key, val = m.group(1), m.group(2).strip()
            current_list_key = None
            if val == '':
                current_list_key = key
                result[key] = []
            elif val.lower() in ('true', 'false'):
                result[key] = val.lower() == 'true'
            elif val.isdigit():
                result[key] = int(val)
            else:
                result[key] = val.strip('"').strip("'")
    return result


def load_config(project_data_dir: str = None) -> dict:
    """Load global config, merged with per-project overrides."""
    global_path = os.path.expanduser("~/.context-hooks/config.yaml")
    config = {}
    if os.path.exists(global_path):
        with open(global_path) as f:
            config = _parse_simple_yaml(f.read())
    if project_data_dir:
        project_path = os.path.join(project_data_dir, "config.yaml")
        if os.path.exists(project_path):
            with open(project_path) as f:
                overrides = _parse_simple_yaml(f.read())
            config.update(overrides)
    return config


def save_config_key(key: str, value, project_data_dir: str = None):
    """Write a single key to global or project config."""
    path = os.path.expanduser("~/.context-hooks/config.yaml")
    if project_data_dir:
        path = os.path.join(project_data_dir, "config.yaml")
    config = {}
    if os.path.exists(path):
        with open(path) as f:
            config = _parse_simple_yaml(f.read())
    config[key] = value
    lines = []
    for k, v in config.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
